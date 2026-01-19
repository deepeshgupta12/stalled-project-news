from __future__ import annotations

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


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def _project_tokens(project_name: str) -> List[str]:
    pn = _normalize(project_name)
    toks = [t for t in re.split(r"[^a-z0-9]+", pn) if t]
    return [t for t in toks if len(t) >= 3]


def _doc_relevance_ok(*, blob: str, project_name: str, city: str, rera_id: Optional[str]) -> bool:
    b = _normalize(blob)

    if rera_id:
        rid = _normalize(rera_id)
        rid2 = rid.replace("/", " ").replace("-", " ")
        if rid in b or rid2 in b:
            return True

    pn = _normalize(project_name)
    if pn and pn in b:
        return True

    toks = _project_tokens(project_name)
    hits = sum(1 for t in set(toks) if t in b)
    c = _normalize(city)
    if hits >= 2 and (not c or c in b):
        return True

    return False


def _normalize_evidence(evidence_any: Any, run_dir: Path) -> List[Dict[str, Any]]:
    """
    evidence.json can be:
      - list[dict] (old)
      - dict with "docs" (new wide)
    Returns list of dicts in this canonical shape:
      { id, url, finalUrl, domain, snippet, textPath, textChars }
    """
    if isinstance(evidence_any, list):
        return evidence_any

    if isinstance(evidence_any, dict) and isinstance(evidence_any.get("docs"), list):
        out: List[Dict[str, Any]] = []
        for d in evidence_any["docs"]:
            if not isinstance(d, dict):
                continue
            doc_id = (d.get("doc_id") or d.get("id") or "").strip()
            url = (d.get("url") or "").strip()
            final_url = (d.get("final_url") or d.get("finalUrl") or url).strip()
            domain = (d.get("domain") or "").strip()
            snippet = (d.get("snippet") or "").strip()
            text_path = (d.get("text_path") or d.get("textPath") or "").strip()

            text_chars = 0
            try:
                tp = Path(text_path) if text_path else None
                if tp and tp.exists():
                    text_chars = len(tp.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                text_chars = 0

            out.append(
                {
                    "id": doc_id,
                    "url": url,
                    "finalUrl": final_url,
                    "domain": domain,
                    "snippet": snippet,
                    "textPath": text_path,
                    "textChars": text_chars,
                }
            )
        return out

    return []


def _normalize_events(events_any: Any) -> List[Dict[str, Any]]:
    """
    events_deduped.json comes from event_extractor.store_timeline()
    shape: {date, claim, confidence, tags, source:{domain,url,final_url,doc_id,text_path,snippet}}
    We normalize it to also have `evidence` key so older code paths work.
    """
    if not isinstance(events_any, list):
        return []

    out: List[Dict[str, Any]] = []
    for ev in events_any:
        if not isinstance(ev, dict):
            continue
        src = ev.get("source") or {}
        if not isinstance(src, dict):
            src = {}
        evidence = {
            "domain": src.get("domain"),
            "url": src.get("url"),
            "final_url": src.get("final_url"),
            "doc_id": src.get("doc_id"),
            "text_path": src.get("text_path"),
            "snippet": src.get("snippet"),
        }
        ev2 = dict(ev)
        ev2["evidence"] = evidence
        out.append(ev2)
    return out


def _pick_primary_source(evidence: List[Dict[str, Any]], events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Pick a primary dated source:
    - Prefer govt/nic domains if they have dates (official status)
    - Else pick earliest dated event source
    """
    ev_by_id = {e.get("id"): e for e in evidence if isinstance(e, dict) and e.get("id")}

    for ev in events:
        ed = ev.get("evidence") or {}
        doc_id = ed.get("doc_id")
        if not doc_id:
            continue
        e = ev_by_id.get(doc_id)
        if not e:
            continue
        dom = (e.get("domain") or host_from_url(e.get("finalUrl") or e.get("url") or "") or "").lower()
        if dom.endswith("gov.in") or dom.endswith("nic.in"):
            return {
                "date": ev.get("date"),
                "domain": dom,
                "url": e.get("finalUrl") or e.get("url"),
                "ref": doc_id,
            }

    if events:
        ev0 = events[0]
        ed = ev0.get("evidence") or {}
        doc_id = ed.get("doc_id")
        if doc_id and doc_id in ev_by_id:
            e = ev_by_id[doc_id]
            dom = (e.get("domain") or host_from_url(e.get("finalUrl") or e.get("url") or "") or "")
            return {"date": ev0.get("date"), "domain": dom, "url": e.get("finalUrl") or e.get("url"), "ref": doc_id}

    return {"date": None, "domain": None, "url": None, "ref": None}


def _domain_diversity_pack(evidence: List[Dict[str, Any]], events: List[Dict[str, Any]], max_domains: int = 6) -> Dict[str, Any]:
    domains: Dict[str, List[Dict[str, Any]]] = {}
    for e in evidence:
        if not isinstance(e, dict):
            continue
        dom = (e.get("domain") or "").strip().lower()
        if not dom:
            dom = host_from_url(e.get("finalUrl") or e.get("url") or "") or "unknown"
        domains.setdefault(dom, []).append(e)

    def dom_rank(d: str) -> int:
        if d.endswith("gov.in") or d.endswith("nic.in"):
            return 0
        if any(x in d for x in ["timesofindia", "indiatimes", "economictimes", "livemint", "thehindu", "indianexpress", "hindustantimes", "moneycontrol", "ndtv", "indiatoday", "business-standard", "financialexpress"]):
            return 1
        return 2

    dom_list = sorted(domains.keys(), key=lambda d: (dom_rank(d), -len(domains[d]), d))[:max_domains]

    ev_pack: List[Dict[str, Any]] = []
    for d in dom_list:
        items = domains[d]
        items_sorted = sorted(items, key=lambda x: x.get("textChars", 0), reverse=True)[:3]
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

    news_candidates: List[Dict[str, Any]] = []
    for e in evidence:
        if not isinstance(e, dict):
            continue
        dom = (e.get("domain") or "").lower()
        if any(x in dom for x in ["indiatimes", "timesofindia", "economictimes", "livemint", "thehindu", "indianexpress", "hindustantimes", "moneycontrol", "ndtv", "indiatoday", "business-standard", "financialexpress"]):
            news_candidates.append(
                {
                    "domain": dom,
                    "url": e.get("finalUrl") or e.get("url"),
                    "title": e.get("title"),
                    "publishedDate": e.get("publishedDate"),
                    "snippets": (e.get("snippets") or [])[:4],
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
        ed = ev.get("evidence") or {}
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


def build_news_with_openai(
    *,
    project: ProjectInput,
    run_dir: Path,
    events_deduped_path: Path,
) -> Tuple[Path, Path, Path, Path]:
    run_dir = run_dir.resolve()
    evidence_path = run_dir / "evidence.json"
    evidence_any = _load_json(evidence_path) if evidence_path.exists() else []
    evidence_docs = _normalize_evidence(evidence_any, run_dir)

    events_any = _load_json(events_deduped_path)
    events = _normalize_events(events_any)

    # --------- RELEVANCE FILTER EVIDENCE (prevents NIC noise) ----------
    filtered_evidence: List[Dict[str, Any]] = []
    for d in evidence_docs:
        blob = f"{d.get('snippet') or ''}\n{d.get('url') or ''}\n{d.get('finalUrl') or ''}"
        # try adding some text head if available (cheap)
        tp = d.get("textPath")
        if tp:
            try:
                t = Path(tp).read_text(encoding="utf-8", errors="replace")
                blob += "\n" + t[:3000]
            except Exception:
                pass

        if _doc_relevance_ok(blob=blob, project_name=project.project_name, city=project.city, rera_id=project.rera_id):
            filtered_evidence.append(d)

    # If filtering accidentally drops everything, fall back (but this should not happen with your RERA docs)
    evidence_use = filtered_evidence or evidence_docs

    primary = _pick_primary_source(evidence_use, events)
    pack = _domain_diversity_pack(evidence_use, events)

    generated_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    valid_until = (datetime.utcnow().replace(microsecond=0) + timedelta(days=7)).isoformat() + "Z"

    # IMPORTANT: include the word "json" to satisfy OpenAI response_format requirements
    system = (
        "You are generating a factual, evidence-bounded project update for a stalled/delayed real-estate project in India. "
        "Hard rule: you MUST NOT invent facts. Every factual claim must be supported by at least one provided snippet. "
        "Return strictly valid JSON only."
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
            "If multiple domains exist, ensure the timeline/newsCoverage cites multiple domains (diversity) when possible.",
        ],
        "citationRules": [
            "Use only refs provided in inputs.timeline (ref/doc_id) or inputs.domains.items[].id",
            "Do not cite a ref you cannot tie to a snippet.",
        ],
    }

    news = openai_chat_json(system=system, user=json.dumps(user_obj, ensure_ascii=False))

    # Collect refs used
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

    ev_by_id = {e.get("id"): e for e in evidence_use if isinstance(e, dict) and e.get("id")}
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

    out_news_json = run_dir / "news.json"
    out_inputs_json = run_dir / "news_inputs.json"
    out_raw_json = run_dir / "news_llm_raw.json"
    out_html = run_dir / "news.html"

    out_news_json.write_text(json.dumps(news, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    out_inputs_json.write_text(json.dumps(user_obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    out_raw_json.write_text(json.dumps({"ok": True}, indent=2) + "\n", encoding="utf-8")

    # HTML render
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