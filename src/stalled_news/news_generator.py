from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional
import json
import re

from .models import ProjectInput
from .openai_client import openai_chat_json
from .whitelist import host_from_url


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def _tokenize(s: str) -> List[str]:
    s = _norm(s)
    parts = [p for p in re.split(r"[^a-z0-9]+", s) if p]
    return parts


def _build_project_matcher(project: ProjectInput):
    pname = _norm(project.project_name)
    city = _norm(project.city)
    rera = _norm(project.rera_id or "")

    # tokens for loose matching
    p_tokens = [t for t in _tokenize(pname) if len(t) >= 3]
    c_tokens = [t for t in _tokenize(city) if len(t) >= 3]
    rera_compact = re.sub(r"[^a-z0-9]+", "", rera)

    def is_relevant(text: str) -> bool:
        t = _norm(text)
        if not t:
            return False

        # strongest signal: rera id (compact)
        if rera_compact and rera_compact in re.sub(r"[^a-z0-9]+", "", t):
            return True

        # project name: require at least 2 meaningful tokens if available
        if len(p_tokens) >= 2 and sum(1 for tok in p_tokens[:6] if tok in t) >= 2:
            return True
        if len(p_tokens) == 1 and p_tokens[0] in t:
            return True

        # fallback: project token + city token
        if p_tokens and c_tokens and (p_tokens[0] in t) and any(ct in t for ct in c_tokens[:3]):
            return True

        return False

    return is_relevant


def _normalize_evidence(evidence_any: Any, project: ProjectInput) -> List[Dict[str, Any]]:
    """
    Supports:
      - old format: list[dict]
      - new wide format: {"project":..., "meta":..., "counts":..., "docs":[...]}
    Returns a list of dicts with consistent keys used by news_generator:
      id, url, finalUrl, domain, snippets(list[str]), textChars(int), needsOcr(bool),
      title(optional), publishedDate(optional)
    """
    is_relevant = _build_project_matcher(project)

    # Unwrap wide
    if isinstance(evidence_any, dict) and isinstance(evidence_any.get("docs"), list):
        docs = evidence_any.get("docs") or []
    elif isinstance(evidence_any, list):
        docs = evidence_any
    else:
        docs = []

    out: List[Dict[str, Any]] = []
    for d in docs:
        if not isinstance(d, dict):
            continue

        doc_id = (d.get("id") or d.get("doc_id") or d.get("docId") or "").strip()
        url = (d.get("url") or "").strip()
        final_url = (d.get("finalUrl") or d.get("final_url") or d.get("finalUrl") or "").strip() or url
        domain = (d.get("domain") or "").strip()
        snippet = (d.get("snippet") or "").strip()

        # Some pipelines store text_path; some store textPath
        text_path = (d.get("text_path") or d.get("textPath") or "").strip()

        # Prefer a known domain if missing
        if not domain:
            domain = host_from_url(final_url or url) or ""

        # Snippets: old format may have "snippets" list
        snippets = d.get("snippets")
        if isinstance(snippets, list):
            snips = [str(x) for x in snippets if str(x).strip()]
        else:
            snips = [snippet] if snippet else []

        # textChars: old format might have it; else try file size
        text_chars = 0
        if isinstance(d.get("textChars"), int):
            text_chars = int(d.get("textChars") or 0)
        elif text_path:
            try:
                tp = Path(text_path)
                if tp.exists():
                    # approximate chars by file bytes; good enough for ranking
                    text_chars = max(0, int(tp.stat().st_size))
            except Exception:
                text_chars = 0

        item = {
            "id": doc_id or "",
            "url": url,
            "finalUrl": final_url,
            "domain": domain,
            "snippet": snippet,
            "snippets": snips,
            "textChars": text_chars,
            "needsOcr": bool(d.get("needsOcr", False)),
            "title": d.get("title"),
            "publishedDate": d.get("publishedDate"),
            "textPath": text_path,
        }

        # Relevance filter (critical to stop random gov PDFs hijacking the pack)
        blob = " ".join([final_url, url, domain, snippet] + snips)
        if not is_relevant(blob):
            # also try first ~2KB of extracted text if available (cheap)
            if text_path:
                try:
                    tp = Path(text_path)
                    if tp.exists():
                        head = tp.read_text(encoding="utf-8", errors="replace")[:2000]
                        if not is_relevant(head):
                            continue
                    else:
                        continue
                except Exception:
                    continue
            else:
                continue

        out.append(item)

    # de-dupe by id or url
    seen = set()
    deduped = []
    for e in out:
        k = e.get("id") or e.get("finalUrl") or e.get("url")
        if not k or k in seen:
            continue
        seen.add(k)
        deduped.append(e)
    return deduped


def _event_source(ev: Dict[str, Any]) -> Dict[str, Any]:
    """
    Supports:
      - events produced by store_timeline(): key = "source"
      - older format: key = "evidence"
    Returns normalized dict with: doc_id, domain, url, final_url, snippet
    """
    if not isinstance(ev, dict):
        return {}
    s = ev.get("source")
    if isinstance(s, dict):
        return {
            "doc_id": s.get("doc_id"),
            "domain": s.get("domain"),
            "url": s.get("url"),
            "final_url": s.get("final_url"),
            "snippet": s.get("snippet"),
        }
    e = ev.get("evidence")
    if isinstance(e, dict):
        return {
            "doc_id": e.get("doc_id"),
            "domain": e.get("domain"),
            "url": e.get("url"),
            "final_url": e.get("final_url") or e.get("finalUrl"),
            "snippet": e.get("snippet"),
        }
    return {}


def _pick_primary_source(evidence: List[Dict[str, Any]], events: List[Dict[str, Any]], project: ProjectInput) -> Dict[str, Any]:
    """
    Pick a primary dated source:
    - Prefer gov/nic domains that are relevant to the project
    - Else pick earliest dated event source among relevant ones
    """
    is_relevant = _build_project_matcher(project)

    ev_by_id = {e.get("id"): e for e in evidence if isinstance(e, dict) and e.get("id")}

    # Prefer govt/nic based on event source
    for ev in events:
        src = _event_source(ev)
        doc_id = src.get("doc_id")
        dom = _norm(src.get("domain") or "")
        url = src.get("final_url") or src.get("url") or ""
        blob = " ".join([str(ev.get("claim") or ""), str(src.get("snippet") or ""), url, dom])
        if not is_relevant(blob):
            continue
        if dom.endswith("gov.in") or dom.endswith("nic.in"):
            if doc_id and doc_id in ev_by_id:
                e = ev_by_id[doc_id]
                dom2 = _norm(e.get("domain") or host_from_url(e.get("finalUrl") or e.get("url") or "") or "")
                return {"date": ev.get("date"), "domain": dom2, "url": e.get("finalUrl") or e.get("url"), "ref": doc_id}
            return {"date": ev.get("date"), "domain": dom, "url": url, "ref": doc_id}

    # fallback: first relevant event
    for ev in events:
        src = _event_source(ev)
        doc_id = src.get("doc_id")
        dom = _norm(src.get("domain") or "")
        url = src.get("final_url") or src.get("url") or ""
        blob = " ".join([str(ev.get("claim") or ""), str(src.get("snippet") or ""), url, dom])
        if not is_relevant(blob):
            continue
        if doc_id and doc_id in ev_by_id:
            e = ev_by_id[doc_id]
            dom2 = _norm(e.get("domain") or host_from_url(e.get("finalUrl") or e.get("url") or "") or "")
            return {"date": ev.get("date"), "domain": dom2, "url": e.get("finalUrl") or e.get("url"), "ref": doc_id}
        return {"date": ev.get("date"), "domain": dom, "url": url, "ref": doc_id}

    return {"date": None, "domain": None, "url": None, "ref": None}


def _domain_diversity_pack(
    evidence: List[Dict[str, Any]],
    events: List[Dict[str, Any]],
    project: ProjectInput,
    max_domains: int = 6,
) -> Dict[str, Any]:
    """
    Prepare a compact pack of material for the LLM:
    - group evidence by domain
    - include a few snippets per domain
    - include timeline events (snippet-backed)
    """
    domains: Dict[str, List[Dict[str, Any]]] = {}
    for e in evidence:
        dom = (e.get("domain") or "").strip().lower()
        if not dom:
            dom = host_from_url(e.get("finalUrl") or e.get("url") or "") or "unknown"
        domains.setdefault(dom, []).append(e)

    def dom_rank(d: str) -> int:
        if d.endswith("gov.in") or d.endswith("nic.in"):
            return 0
        if any(x in d for x in [
            "timesofindia", "indiatimes", "economictimes", "livemint", "thehindu",
            "indianexpress", "hindustantimes", "moneycontrol", "ndtv", "indiatoday",
            "business-standard", "financialexpress"
        ]):
            return 1
        return 2

    # sort by rank then by best text size, then by count
    dom_list = sorted(
        domains.keys(),
        key=lambda d: (
            dom_rank(d),
            -max([it.get("textChars", 0) for it in domains[d]] or [0]),
            -len(domains[d]),
            d,
        ),
    )[:max_domains]

    ev_pack: List[Dict[str, Any]] = []
    for d in dom_list:
        items = domains[d]
        items_sorted = sorted(items, key=lambda x: x.get("textChars", 0), reverse=True)[:3]
        ev_pack.append({
            "domain": d,
            "items": [
                {
                    "id": it.get("id"),
                    "url": it.get("finalUrl") or it.get("url"),
                    "title": it.get("title"),
                    "publishedDate": it.get("publishedDate"),
                    "snippets": (it.get("snippets") or [])[:4],
                    "needsOcr": it.get("needsOcr", False),
                    "textChars": it.get("textChars", 0),
                }
                for it in items_sorted
            ]
        })

    # “news coverage candidates”
    news_candidates: List[Dict[str, Any]] = []
    for e in evidence:
        dom = (e.get("domain") or "").lower()
        if any(x in dom for x in [
            "indiatimes", "timesofindia", "economictimes", "livemint", "thehindu",
            "indianexpress", "hindustantimes", "moneycontrol", "ndtv", "indiatoday",
            "business-standard", "financialexpress"
        ]):
            news_candidates.append({
                "domain": dom,
                "url": e.get("finalUrl") or e.get("url"),
                "title": e.get("title"),
                "publishedDate": e.get("publishedDate"),
                "snippets": (e.get("snippets") or [])[:4],
                "ref": e.get("id"),
            })

    seen = set()
    news_out = []
    for n in news_candidates:
        u = n.get("url")
        if not u or u in seen:
            continue
        seen.add(u)
        news_out.append(n)
    news_out = news_out[:8]

    # Timeline from extracted events: normalize source/evidence
    timeline = []
    for ev in events[:30]:
        src = _event_source(ev)
        timeline.append({
            "date": ev.get("date"),
            "claim": ev.get("claim"),
            "ref": src.get("doc_id"),
            "domain": src.get("domain"),
            "url": src.get("final_url") or src.get("url"),
            "snippet": src.get("snippet"),
        })

    return {
        "domains": ev_pack,
        "timeline": timeline,
        "newsCoverageCandidates": news_out,
    }


def build_news_with_openai(
    *,
    project: ProjectInput,
    run_dir: Path,
    events_deduped_path: Path,
) -> Tuple[Path, Path, Path, Path]:
    run_dir = run_dir.resolve()
    evidence_path = run_dir / "evidence.json"
    evidence_any = _load_json(evidence_path) if evidence_path.exists() else []
    events = _load_json(events_deduped_path)

    if not isinstance(events, list):
        raise ValueError(f"events file must be a list, got: {type(events)} at {events_deduped_path}")

    evidence = _normalize_evidence(evidence_any, project)

    primary = _pick_primary_source(evidence, events, project)
    pack = _domain_diversity_pack(evidence, events, project)

    generated_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    valid_until = (datetime.utcnow().replace(microsecond=0) + timedelta(days=7)).isoformat() + "Z"

    # IMPORTANT: OpenAI json_object requires 'json' word in messages.
    system = (
        "You are generating a factual, evidence-bounded project update for a stalled/delayed real-estate project in India. "
        "Hard rule: you MUST NOT invent facts. Every factual claim must be supported by at least one provided snippet. "
        "If evidence is insufficient, write 'Insufficient evidence' and do not guess. "
        "Return ONLY valid JSON."
    )

    user = {
        "project": {"name": project.project_name, "city": project.city, "reraId": project.rera_id},
        "primarySourceHint": primary,
        "inputs": pack,
        "outputSchema": {
            "headline": "string",
            "shortSummary": "2-3 line summary",
            "detailedSummary": "500-1000 words, multi-paragraph, human-written",
            "primaryDateSource": {"date": "YYYY-MM-DD or null", "domain": "string or null", "ref": "E# id", "url": "string"},
            "timeline": [{"date": "YYYY-MM-DD", "event": "string", "ref": "E# id"}],
            "latestUpdate": {"date": "YYYY-MM-DD or null", "update": "string", "ref": "E# id"},
            "buyerImplications": ["bullets, must be grounded in evidence or clearly framed as guidance"],
            "investorImplications": ["bullets, must be grounded in evidence or clearly framed as guidance"],
            "newsCoverage": [{"title": "string", "date": "YYYY-MM-DD or null", "sourceDomain": "string", "ref": "E# id"}],
            "sources": [{"ref": "E# id", "domain": "string", "urlText": "plain text only (no hyperlink)"}],
            "generatedAt": generated_at,
            "validUntil": valid_until,
        },
        "styleRules": [
            "Write like a human analyst: vary sentence length, avoid generic AI phrases, be specific where evidence exists.",
            "Do not overclaim: if only regulator records exist, say so and avoid dramatic language.",
            "Cater to BOTH buyers and investors in separate sections.",
            "If multiple domains exist in inputs.domains, cite at least 2 distinct domains when possible.",
        ],
        "citationRules": [
            "Use only refs provided in inputs.timeline (ref/doc_id) or inputs.domains.items[].id",
            "Do not cite a ref you cannot tie to a snippet.",
        ],
    }

    news = openai_chat_json(system=system, user=json.dumps(user, ensure_ascii=False))

    # Collect refs used in the model output
    used_refs = set()

    def _collect(obj: Any):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "ref" and isinstance(v, str):
                    used_refs.add(v)
                _collect(v)
        elif isinstance(obj, list):
            for x in obj:
                _collect(x)

    _collect(news)

    ev_by_id = {e.get("id"): e for e in evidence if isinstance(e, dict) and e.get("id")}
    sources = []
    for ref in sorted(list(used_refs)):
        e = ev_by_id.get(ref)
        if not e:
            continue
        u = e.get("finalUrl") or e.get("url")
        dom = e.get("domain") or host_from_url(u) or ""
        sources.append({"ref": ref, "domain": dom, "urlText": u})

    news["sources"] = sources
    news["generatedAt"] = generated_at
    news["validUntil"] = valid_until

    # Save artifacts
    out_news_json = run_dir / "news.json"
    out_inputs_json = run_dir / "news_inputs.json"
    out_raw_json = run_dir / "news_llm_raw.json"
    out_html = run_dir / "news.html"

    out_news_json.write_text(json.dumps(news, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    out_inputs_json.write_text(json.dumps(user, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    out_raw_json.write_text(json.dumps({"ok": True}, indent=2) + "\n", encoding="utf-8")

    # HTML render (no backlinks)
    def esc(s: Any) -> str:
        x = "" if s is None else str(s)
        return x.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    headline = esc(news.get("headline"))
    short = esc(news.get("shortSummary"))
    detailed = news.get("detailedSummary") or ""
    paras = [p.strip() for p in str(detailed).split("\n\n") if p.strip()]
    detailed_html = "\n".join([f"<p>{esc(p)}</p>" for p in paras])

    prim = news.get("primaryDateSource") or {}
    prim_line = f"{esc(prim.get('date'))} — {esc(prim.get('domain'))} — {esc(prim.get('url'))} ({esc(prim.get('ref'))})"

    timeline_items = news.get("timeline") or []
    timeline_html = "\n".join([f"<li><b>{esc(it.get('date'))}</b> — {esc(it.get('event'))} ({esc(it.get('ref'))})</li>" for it in timeline_items])

    latest = news.get("latestUpdate") or {}
    latest_html = f"<b>{esc(latest.get('date'))}</b> — {esc(latest.get('update'))} ({esc(latest.get('ref'))})"

    buyer = news.get("buyerImplications") or []
    buyer_html = "\n".join([f"<li>{esc(x)}</li>" for x in buyer])

    investor = news.get("investorImplications") or []
    investor_html = "\n".join([f"<li>{esc(x)}</li>" for x in investor])

    coverage = news.get("newsCoverage") or []
    coverage_html = "\n".join([f"<li>{esc(x.get('title'))} — {esc(x.get('sourceDomain'))} — {esc(x.get('date'))} ({esc(x.get('ref'))})</li>" for x in coverage])

    sources_html = "\n".join([f"<li>{esc(s.get('ref'))} — {esc(s.get('domain'))} — {esc(s.get('urlText'))}</li>" for s in sources])

    footer = f"GeneratedAt: {esc(news.get('generatedAt'))} | ValidUntil: {esc(news.get('validUntil'))}"

    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>{headline}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Helvetica, Arial, sans-serif; margin: 28px; color: #111; }}
    h1 {{ margin-bottom: 6px; }}
    .meta {{ color: #555; margin-bottom: 18px; }}
    .card {{ border: 1px solid #e6e6e6; border-radius: 10px; padding: 14px 16px; margin: 12px 0; }}
    .label {{ font-weight: 700; margin-bottom: 8px; }}
    ul {{ margin-top: 6px; }}
    .footer {{ margin-top: 18px; color: #666; font-size: 12px; }}
    code {{ background: #f6f6f6; padding: 2px 6px; border-radius: 6px; }}
  </style>
</head>
<body>
  <h1>{headline}</h1>
  <div class="meta">Project: <b>{esc(project.project_name)}</b> | City: <b>{esc(project.city)}</b> | RERA: <b>{esc(project.rera_id)}</b></div>

  <div class="card">
    <div class="label">2–3 line summary</div>
    <div>{short}</div>
  </div>

  <div class="card">
    <div class="label">Detailed summary</div>
    {detailed_html}
  </div>

  <div class="card">
    <div class="label">Date and Source (Primary)</div>
    <div><code>{prim_line}</code></div>
  </div>

  <div class="card">
    <div class="label">Timeline of key events</div>
    <ul>{timeline_html}</ul>
  </div>

  <div class="card">
    <div class="label">Latest update</div>
    <div>{latest_html}</div>
  </div>

  <div class="card">
    <div class="label">What it means for buyers</div>
    <ul>{buyer_html}</ul>
  </div>

  <div class="card">
    <div class="label">What it means for investors</div>
    <ul>{investor_html}</ul>
  </div>

  <div class="card">
    <div class="label">News coverage (references only)</div>
    <ul>{coverage_html}</ul>
  </div>

  <div class="card">
    <div class="label">Sources (references only, no backlinks)</div>
    <ul>{sources_html}</ul>
  </div>

  <div class="footer">{footer}</div>
</body>
</html>
"""
    out_html.write_text(html, encoding="utf-8")

    return out_news_json, out_html, out_inputs_json, out_raw_json