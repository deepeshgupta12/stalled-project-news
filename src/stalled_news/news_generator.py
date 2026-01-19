from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional
import json
import os

from .models import ProjectInput
from .openai_client import openai_chat_json
from .whitelist import host_from_url


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_read_text_chars(p: Optional[str]) -> int:
    try:
        if not p:
            return 0
        fp = Path(p)
        if not fp.exists():
            return 0
        return len(fp.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return 0


def _normalize_evidence(evidence_raw: Any) -> List[Dict[str, Any]]:
    """
    Supports:
      - Old format: list[dict] (already normalized)
      - New format: dict with keys: {project, meta, serp_results_path, counts, docs:[...]}
        where docs contain: {doc_id,url,final_url,domain,snippet,text_path}
    Returns a list of evidence dicts with keys used by generator:
      id, url, finalUrl, domain, snippets, textPath, textChars, title, publishedDate, needsOcr
    """
    # Old format
    if isinstance(evidence_raw, list):
        out: List[Dict[str, Any]] = []
        for e in evidence_raw:
            if isinstance(e, dict):
                out.append(e)
        return out

    # New format
    if isinstance(evidence_raw, dict) and isinstance(evidence_raw.get("docs"), list):
        out: List[Dict[str, Any]] = []
        for d in evidence_raw["docs"]:
            if not isinstance(d, dict):
                continue

            doc_id = (d.get("id") or d.get("doc_id") or "").strip()
            url = (d.get("url") or "").strip()
            final_url = (d.get("finalUrl") or d.get("final_url") or d.get("finalUrl") or url).strip()
            domain = (d.get("domain") or "").strip().lower()
            if not domain:
                domain = (host_from_url(final_url or url) or "").lower()

            snippet = (d.get("snippet") or "").strip()
            text_path = (d.get("textPath") or d.get("text_path") or "").strip()

            out.append(
                {
                    "id": doc_id,
                    "url": url,
                    "finalUrl": final_url,
                    "domain": domain,
                    # old pipeline used "snippets" as list
                    "snippets": [snippet] if snippet else [],
                    "textPath": text_path,
                    "textChars": _safe_read_text_chars(text_path),
                    "title": d.get("title"),
                    "publishedDate": d.get("publishedDate"),
                    "needsOcr": bool(d.get("needsOcr", False)),
                }
            )
        return out

    # Unknown
    return []


def _normalize_events(events_raw: Any) -> List[Dict[str, Any]]:
    """
    Your events_deduped.json has:
      {date, claim, confidence, tags, source:{domain,url,final_url,doc_id,text_path,snippet}}
    Older generator expects:
      {date, claim, ..., evidence:{domain,url,final_url,doc_id,snippet}}
    """
    if not isinstance(events_raw, list):
        return []

    out: List[Dict[str, Any]] = []
    for ev in events_raw:
        if not isinstance(ev, dict):
            continue

        # already in old shape
        if isinstance(ev.get("evidence"), dict):
            out.append(ev)
            continue

        src = ev.get("source")
        if isinstance(src, dict):
            ev2 = dict(ev)
            ev2["evidence"] = {
                "domain": src.get("domain"),
                "url": src.get("url"),
                "final_url": src.get("final_url") or src.get("finalUrl") or src.get("url"),
                "doc_id": src.get("doc_id"),
                "snippet": src.get("snippet"),
            }
            out.append(ev2)
        else:
            out.append(ev)

    return out


def _pick_primary_source(evidence: List[Dict[str, Any]], events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Pick a primary dated source:
    - Prefer govt/nic domains if they have dates (official status)
    - Else pick earliest dated event source
    """
    ev_by_id = {e.get("id"): e for e in evidence if isinstance(e, dict) and e.get("id")}

    # Prefer govt/nic among dated events
    for ev in events:
        if not isinstance(ev, dict):
            continue
        ed = ev.get("evidence") or {}
        if not isinstance(ed, dict):
            continue
        doc_id = ed.get("doc_id")
        if not doc_id:
            continue

        e = ev_by_id.get(doc_id)
        if not isinstance(e, dict):
            continue

        dom = (e.get("domain") or host_from_url(e.get("finalUrl") or e.get("url") or "") or "").lower()
        if dom.endswith("gov.in") or dom.endswith("nic.in"):
            return {
                "date": ev.get("date"),
                "domain": dom,
                "url": e.get("finalUrl") or e.get("url"),
                "ref": doc_id,
            }

    # fallback: first event if it maps to evidence
    if events:
        ev0 = events[0]
        if isinstance(ev0, dict):
            ed = ev0.get("evidence") or {}
            if isinstance(ed, dict):
                doc_id = ed.get("doc_id")
                if doc_id and doc_id in ev_by_id:
                    e = ev_by_id[doc_id]
                    dom = (e.get("domain") or host_from_url(e.get("finalUrl") or e.get("url") or "") or "")
                    return {
                        "date": ev0.get("date"),
                        "domain": dom,
                        "url": e.get("finalUrl") or e.get("url"),
                        "ref": doc_id,
                    }

    return {"date": None, "domain": None, "url": None, "ref": None}


def _domain_diversity_pack(evidence: List[Dict[str, Any]], events: List[Dict[str, Any]], max_domains: int = 6) -> Dict[str, Any]:
    """
    Prepare a compact pack for the LLM:
    - group evidence by domain
    - include a few snippets per domain
    - include timeline events (snippet-backed)
    """
    domains: Dict[str, List[Dict[str, Any]]] = {}
    for e in evidence:
        if not isinstance(e, dict):
            continue
        dom = (e.get("domain") or "").strip().lower()
        if not dom:
            dom = (host_from_url(e.get("finalUrl") or e.get("url") or "") or "unknown").lower()
        domains.setdefault(dom, []).append(e)

    def dom_rank(d: str) -> int:
        if d.endswith("gov.in") or d.endswith("nic.in"):
            return 0
        # trusted-ish Indian media bucket
        if any(x in d for x in [
            "timesofindia", "indiatimes", "economictimes", "livemint", "thehindu",
            "indianexpress", "hindustantimes", "moneycontrol", "ndtv", "indiatoday",
            "business-standard", "financialexpress"
        ]):
            return 1
        return 2

    dom_list = sorted(domains.keys(), key=lambda d: (dom_rank(d), -len(domains[d]), d))[:max_domains]

    ev_pack: List[Dict[str, Any]] = []
    for d in dom_list:
        items = domains[d]
        items_sorted = sorted(items, key=lambda x: x.get("textChars", 0) or 0, reverse=True)[:3]
        ev_pack.append(
            {
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
                ],
            }
        )

    # News candidates (for the model to cite when available)
    news_candidates: List[Dict[str, Any]] = []
    for e in evidence:
        if not isinstance(e, dict):
            continue
        dom = (e.get("domain") or "").lower()
        if any(x in dom for x in [
            "indiatimes", "timesofindia", "economictimes", "livemint", "thehindu",
            "indianexpress", "hindustantimes", "moneycontrol", "ndtv", "indiatoday",
            "business-standard", "financialexpress"
        ]):
            news_candidates.append(
                {
                    "domain": dom,
                    "url": e.get("finalUrl") or e.get("url"),
                    "title": e.get("title"),
                    "publishedDate": e.get("publishedDate"),
                    "snippets": (e.get("snippets") or [])[:4],
                    "ref": e.get("id"),
                }
            )

    seen = set()
    news_out = []
    for n in news_candidates:
        u = n.get("url")
        if not u or u in seen:
            continue
        seen.add(u)
        news_out.append(n)
    news_out = news_out[:8]

    timeline = []
    for ev in events[:30]:
        if not isinstance(ev, dict):
            continue
        ed = ev.get("evidence") or {}
        if not isinstance(ed, dict):
            ed = {}
        timeline.append(
            {
                "date": ev.get("date"),
                "claim": ev.get("claim"),
                "ref": ed.get("doc_id"),
                "domain": ed.get("domain"),
                "url": ed.get("final_url") or ed.get("url"),
                "snippet": ed.get("snippet"),
            }
        )

    return {"domains": ev_pack, "timeline": timeline, "newsCoverageCandidates": news_out}




def _filter_evidence_for_project(
    evidence: List[Dict[str, Any]],
    events: List[Dict[str, Any]],
    project: ProjectInput,
    *,
    max_docs: int = 40,
) -> List[Dict[str, Any]]:
    """Reduce evidence noise for the LLM pack.

    Strategy:
    - Always keep evidence docs referenced by extracted events.
    - Also keep docs whose title/snippet/url look project-relevant.
    - If nothing matches, fall back to a small slice of original evidence.
    """
    # refs used by extracted events
    refs = set()
    for ev in events or []:
        src = ev.get("evidence") or ev.get("source") or {}
        rid = src.get("doc_id") or src.get("id") or ev.get("ref")
        if isinstance(rid, str) and rid:
            refs.add(rid)

    by_id = {e.get("id"): e for e in evidence if isinstance(e, dict) and e.get("id")}
    kept: List[Dict[str, Any]] = [by_id[r] for r in refs if r in by_id]

    # lightweight keyword relevance (works when RERA id is missing)
    proj = (project.project_name or "").lower()
    city = (project.city or "").lower()
    rera = (project.rera_id or "").lower().replace("/", "")

    toks = [t for t in re.split(r"[^a-z0-9]+", proj) if len(t) >= 3]

    def looks_relevant(e: Dict[str, Any]) -> bool:
        blob = " ".join(
            [
                str(e.get("title") or ""),
                str(e.get("snippet") or ""),
                str(e.get("url") or ""),
                str(e.get("finalUrl") or ""),
            ]
        ).lower()
        blob_norm = blob.replace("/", "")
        if rera and rera in blob_norm:
            return True
        if toks:
            hits = sum(1 for t in toks if t in blob)
            if hits >= min(2, len(toks)):
                return True
            if city and hits >= 1 and city in blob:
                return True
        return False

    for e in evidence:
        if not isinstance(e, dict):
            continue
        if e.get("id") in refs:
            continue
        if looks_relevant(e):
            kept.append(e)

    # de-dupe, cap
    out: List[Dict[str, Any]] = []
    seen = set()
    for e in kept:
        i = e.get("id")
        if not i or i in seen:
            continue
        seen.add(i)
        out.append(e)

    if not out:
        # if we couldn't determine relevance, keep a small slice
        out = [e for e in evidence if isinstance(e, dict)][: max_docs]

    return out[:max_docs]
def build_news_with_openai(
    *,
    project: ProjectInput,
    run_dir: Path,
    events_deduped_path: Path,
) -> Tuple[Path, Path, Path, Path]:
    run_dir = run_dir.resolve()

    evidence_path = run_dir / "evidence.json"
    evidence_raw = _load_json(evidence_path) if evidence_path.exists() else []
    evidence = _normalize_evidence(evidence_raw)

    events_raw = _load_json(events_deduped_path)
    events = _normalize_events(events_raw)

    evidence_for_pack = _filter_evidence_for_project(evidence, events, project, max_docs=40)
    primary = _pick_primary_source(evidence_for_pack, events)
    pack = _domain_diversity_pack(evidence_for_pack, events)

    generated_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    valid_until = (datetime.utcnow().replace(microsecond=0) + timedelta(days=7)).isoformat() + "Z"

    # domain diversity signal
    ev_domains = sorted({(e.get("domain") or "").lower() for e in evidence if isinstance(e, dict) and e.get("domain")})
    has_non_gov = any(d and not (d.endswith("gov.in") or d.endswith("nic.in")) for d in ev_domains)
    enforce_diversity = (len(ev_domains) >= 2) and has_non_gov

    # IMPORTANT: OpenAI json_object requires the word "json" in messages (SDK validation)
    system = (
        "You are generating a factual, evidence-bounded project update for a stalled/delayed real-estate project in India.\n"
        "Return ONLY valid JSON (json object) that matches the provided outputSchema.\n"
        "Hard rule: you MUST NOT invent facts. Every factual claim must be supported by at least one provided snippet.\n"
        "If evidence is insufficient, write 'Insufficient evidence' and do not guess."
    )

    style_rules = [
        "Write like a human analyst: vary sentence length, avoid generic AI phrases, be specific where evidence exists.",
        "Do not overclaim: if only regulator records exist, say so and avoid dramatic language.",
        "Cater to BOTH buyers and investors in separate sections.",
        "Cite only refs that exist in inputs.timeline[].ref or inputs.domains[].items[].id.",
    ]

    if enforce_diversity:
        style_rules.append(
            "Domain diversity requirement: evidence includes non-regulator sources. "
            "Use at least 2 distinct source domains across timeline/newsCoverage/sources where possible, "
            "and ensure newsCoverage includes at least 1 non-gov ref if any exists."
        )
    else:
        style_rules.append(
            "If non-regulator sources are not credible/available in inputs, state clearly that coverage is regulator-only."
        )

    user_obj = {
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
            "buyerImplications": ["bullets; grounded in evidence or clearly framed as guidance"],
            "investorImplications": ["bullets; grounded in evidence or clearly framed as guidance"],
            "newsCoverage": [{"title": "string", "date": "YYYY-MM-DD or null", "sourceDomain": "string", "ref": "E# id"}],
            "sources": [{"ref": "E# id", "domain": "string", "urlText": "plain text only (no hyperlink)"}],
            "generatedAt": generated_at,
            "validUntil": valid_until,
        },
        "styleRules": style_rules,
        "citationRules": [
            "Use only refs provided in inputs.timeline[].ref (doc_id) or inputs.domains[].items[].id.",
            "Do not cite a ref you cannot tie to a snippet.",
        ],
    }

    # Send as JSON string to keep the OpenAI message content as text
    news = openai_chat_json(system=system, user=json.dumps(user_obj, ensure_ascii=False))

    # Collect used refs
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
        dom = (e.get("domain") or host_from_url(u) or "").lower()
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
    out_inputs_json.write_text(json.dumps(user_obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
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
    timeline_html = "\n".join(
        [f"<li><b>{esc(it.get('date'))}</b> — {esc(it.get('event'))} ({esc(it.get('ref'))})</li>" for it in timeline_items]
    )

    latest = news.get("latestUpdate") or {}
    latest_html = f"<b>{esc(latest.get('date'))}</b> — {esc(latest.get('update'))} ({esc(latest.get('ref'))})"

    buyer = news.get("buyerImplications") or []
    buyer_html = "\n".join([f"<li>{esc(x)}</li>" for x in buyer])

    investor = news.get("investorImplications") or []
    investor_html = "\n".join([f"<li>{esc(x)}</li>" for x in investor])

    coverage = news.get("newsCoverage") or []
    coverage_html = "\n".join(
        [f"<li>{esc(x.get('title'))} — {esc(x.get('sourceDomain'))} — {esc(x.get('date'))} ({esc(x.get('ref'))})</li>" for x in coverage]
    )

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