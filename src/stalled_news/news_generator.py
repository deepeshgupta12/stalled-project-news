from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple
import json

from .models import ProjectInput
from .openai_client import openai_chat_json
from .whitelist import host_from_url


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))


def _read_text_chars(text_path: str) -> int:
    try:
        if not text_path:
            return 0
        p = Path(text_path)
        if not p.exists():
            return 0
        return len(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return 0


def _normalize_evidence(evidence_any: Any) -> List[Dict[str, Any]]:
    """
    Normalizes evidence.json into a consistent list-of-dicts shape expected by this module.

    Accepts:
      1) Old list format: [ {id, url, finalUrl, domain, ...}, ... ]
      2) Wide format: {"counts": {...}, "docs": [ {doc_id, url, final_url, domain, snippet, text_path}, ... ]}

    Returns list items with keys (minimum):
      - id
      - url
      - finalUrl
      - domain
      - title
      - publishedDate
      - snippets (list[str])
      - textPath
      - textChars
      - needsOcr
    """
    # If dict -> prefer docs
    if isinstance(evidence_any, dict):
        if isinstance(evidence_any.get("docs"), list):
            evidence_any = evidence_any.get("docs") or []
        else:
            # unknown dict shape
            evidence_any = []

    if not isinstance(evidence_any, list):
        return []

    out: List[Dict[str, Any]] = []
    for e in evidence_any:
        if not isinstance(e, dict):
            continue

        # wide-doc keys
        doc_id = (e.get("doc_id") or e.get("id") or "").strip()
        url = (e.get("url") or "").strip()
        final_url = (e.get("final_url") or e.get("finalUrl") or e.get("finalUrl".lower()) or e.get("finalUrl") or e.get("finalUrl") or "").strip()
        # fallback to other naming
        if not final_url:
            final_url = (e.get("finalUrl") or e.get("final_url") or e.get("finalUrl") or url).strip()

        domain = (e.get("domain") or "").strip()
        if not domain:
            domain = host_from_url(final_url or url or "") or ""

        # old evidence may store snippets differently
        snippet = (e.get("snippet") or "").strip()
        snippets = e.get("snippets")
        if isinstance(snippets, list):
            snippets_out = [str(x) for x in snippets if str(x).strip()]
        else:
            snippets_out = [snippet] if snippet else []

        title = (e.get("title") or "").strip()
        published_date = e.get("publishedDate")

        text_path = (e.get("text_path") or e.get("textPath") or e.get("textPath") or "").strip()
        if not text_path:
            # sometimes stored as text_path already normalized by your Step 6E output
            text_path = (e.get("textPath") or "").strip()

        text_chars = e.get("textChars")
        if not isinstance(text_chars, int):
            text_chars = _read_text_chars(text_path)

        needs_ocr = bool(e.get("needsOcr", False))

        # If old format already uses id/url/finalUrl, keep them
        item = {
            "id": doc_id,
            "url": url,
            "finalUrl": final_url or url,
            "domain": domain,
            "title": title if title else None,
            "publishedDate": published_date if published_date else None,
            "snippets": snippets_out,
            "textPath": text_path if text_path else None,
            "textChars": text_chars,
            "needsOcr": needs_ocr,
        }
        # Keep any extra fields (non-breaking)
        for k, v in e.items():
            if k in item:
                continue
            item[k] = v

        if item["id"]:
            out.append(item)

    return out


def _event_evidence(ev: Dict[str, Any]) -> Dict[str, Any]:
    """
    Your event_extractor outputs:
      - {"date", "claim", "confidence", "tags", "source": {...}}
    but older code expects "evidence".
    This helper unifies both.
    """
    if not isinstance(ev, dict):
        return {}
    ed = ev.get("evidence")
    if isinstance(ed, dict) and ed:
        return ed
    sd = ev.get("source")
    if isinstance(sd, dict) and sd:
        return sd
    return {}


def _pick_primary_source(evidence: List[Dict[str, Any]], events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Pick a primary dated source:
    - Prefer govt/nic domains if they have dates (official status)
    - Else pick earliest dated event source
    """
    # Build lookup for doc_id -> evidence
    ev_by_id = {e.get("id"): e for e in evidence if isinstance(e, dict) and e.get("id")}

    # Prefer official sources among events
    for ev in events:
        ed = _event_evidence(ev)
        doc_id = ed.get("doc_id") or ed.get("docId") or ed.get("id")
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

    # fallback: first event (already sorted in extract_events)
    if events:
        ev0 = events[0]
        ed = _event_evidence(ev0)
        doc_id = ed.get("doc_id") or ed.get("docId") or ed.get("id")
        if doc_id and doc_id in ev_by_id:
            e = ev_by_id[doc_id]
            dom = (e.get("domain") or host_from_url(e.get("finalUrl") or e.get("url") or "") or "")
            return {
                "date": ev0.get("date"),
                "domain": dom,
                "url": e.get("finalUrl") or e.get("url"),
                "ref": doc_id
            }

    return {"date": None, "domain": None, "url": None, "ref": None}


def _domain_diversity_pack(evidence: List[Dict[str, Any]], events: List[Dict[str, Any]], max_domains: int = 6) -> Dict[str, Any]:
    """
    Prepare a compact pack of material for the LLM:
    - group evidence by domain
    - include a few snippets / titles per domain
    - include timeline events (already snippet-backed)
    """
    domains: Dict[str, List[Dict[str, Any]]] = {}
    for e in evidence:
        if not isinstance(e, dict):
            continue
        dom = (e.get("domain") or "").strip().lower()
        if not dom:
            dom = host_from_url(e.get("finalUrl") or e.get("url") or "") or "unknown"
        domains.setdefault(dom, []).append(e)

    # Prefer official + news + rest (heuristic)
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

    dom_list = sorted(domains.keys(), key=lambda d: (dom_rank(d), -len(domains[d]), d))[:max_domains]

    ev_pack: List[Dict[str, Any]] = []
    for d in dom_list:
        items = domains[d]
        items_sorted = sorted(items, key=lambda x: x.get("textChars", 0) or 0, reverse=True)[:3]
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
                } for it in items_sorted
            ]
        })

    # News coverage candidates
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
            news_candidates.append({
                "domain": dom,
                "url": e.get("finalUrl") or e.get("url"),
                "title": e.get("title"),
                "publishedDate": e.get("publishedDate"),
                "snippets": (e.get("snippets") or [])[:4],
                "ref": e.get("id"),
            })

    # de-dupe urls
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
        ed = _event_evidence(ev)
        timeline.append({
            "date": ev.get("date"),
            "claim": ev.get("claim"),
            "ref": ed.get("doc_id") or ed.get("docId") or ed.get("id"),
            "domain": ed.get("domain"),
            "url": ed.get("final_url") or ed.get("finalUrl") or ed.get("url"),
            "snippet": ed.get("snippet"),
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
    evidence = _normalize_evidence(evidence_any)

    events = _load_json(events_deduped_path)
    if not isinstance(events, list):
        raise RuntimeError(f"events_deduped_path must be a list JSON. Got: {type(events)}")

    primary = _pick_primary_source(evidence, events)
    pack = _domain_diversity_pack(evidence, events)

    generated_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    valid_until = (datetime.utcnow().replace(microsecond=0) + timedelta(days=7)).isoformat() + "Z"

    # ----- LLM instruction -----
    system = (
        "You are generating a factual, evidence-bounded project update for a stalled/delayed real-estate project in India. "
        "Hard rule: you MUST NOT invent facts. Every factual claim must be supported by at least one provided snippet. "
        "If evidence is insufficient, write 'Insufficient evidence' and do not guess."
    )

    user = {
        "project": {
            "name": project.project_name,
            "city": project.city,
            "reraId": project.rera_id,
        },
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
            "validUntil": valid_until
        },
        "styleRules": [
            "Write like a human analyst: vary sentence length, avoid generic AI phrases, be specific where evidence exists.",
            "Do not overclaim: if only regulator records exist, say so and avoid dramatic language.",
            "Cater to BOTH buyers and investors in separate sections.",
            "If multiple domains exist, ensure the timeline/newsCoverage cites multiple domains (diversity) when possible."
        ],
        "citationRules": [
            "Use only refs provided in inputs.timeline (ref/doc_id) or inputs.domains.items[].id",
            "Do not cite a ref you cannot tie to a snippet.",
        ],
    }

    news = openai_chat_json(system=system, user=json.dumps(user, ensure_ascii=False))

    # Collect refs used by model
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
        return (x.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

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
