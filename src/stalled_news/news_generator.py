from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .openai_client import chat_completion_json
from .models import ProjectInput


def _utc_now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _valid_until_iso(days: int = 7) -> str:
    return (datetime.utcnow() + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _store_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def _pick_key_events(events: List[Dict[str, Any]], max_events: int = 10) -> List[Dict[str, Any]]:
    """
    Picks a compact, high-signal set of events from events_deduped.json.
    Heuristics:
      - prefer events containing strong tags/keywords
      - ensure date diversity
    """
    if not events:
        return []

    strong_terms = [
        "registration suspended", "suspended", "show-cause", "show cause", "rejection",
        "order", "notice", "penalty", "revoked", "extension", "adjourned", "adjournment"
    ]

    def score(e: Dict[str, Any]) -> float:
        claim = (e.get("claim") or "").lower()
        tags = " ".join(e.get("tags") or []).lower()
        s = float(e.get("confidence") or 0.0)
        boost = 0.0
        for t in strong_terms:
            if t in claim or t in tags:
                boost = max(boost, 0.25)
        # prefer later dates slightly
        date = e.get("date") or ""
        late = 0.05 if date.startswith("2023") or date.startswith("2024") or date.startswith("2025") else 0.0
        return s + boost + late

    ranked = sorted(events, key=lambda e: score(e), reverse=True)

    selected: List[Dict[str, Any]] = []
    used_dates = set()
    for e in ranked:
        d = e.get("date")
        if not d:
            continue
        # allow 1 per date first pass
        if d in used_dates and len(used_dates) < max_events:
            continue
        selected.append(e)
        used_dates.add(d)
        if len(selected) >= max_events:
            break

    # ensure chronological order for downstream rendering
    selected = sorted(selected, key=lambda e: e.get("date") or "")
    return selected


def build_news_with_openai(
    *,
    project: ProjectInput,
    run_dir: Path,
    events_deduped_path: Path,
) -> Tuple[Path, Path, Path, Path]:
    """
    Generates:
      - news.json
      - news.html
      - news_inputs.json
      - news_llm_raw.json
    """
    events = _load_json(events_deduped_path)
    key_events = _pick_key_events(events, max_events=10)

    if not key_events:
        # minimal fallback, still produces output files
        news_obj = {
            "headline": f"No verified public updates found for {project.project_name} ({project.city})",
            "summary": "No whitelisted sources returned extractable, dated evidence in the current run.",
            "primaryDate": None,
            "primarySource": None,
            "timeline": [],
            "latestUpdate": None,
            "buyerImplications": [
                "Try again with additional whitelisted sources or enable OCR for scanned PDFs from RERA."
            ],
            "sources": [],
            "generatedAt": _utc_now_iso(),
            "validUntil": _valid_until_iso(7),
        }
        news_json = run_dir / "news.json"
        news_html = run_dir / "news.html"
        inputs_json = run_dir / "news_inputs.json"
        raw_json = run_dir / "news_llm_raw.json"
        _store_json(news_json, news_obj)
        _store_json(inputs_json, {"project": project.model_dump(), "key_events": key_events})
        _store_json(raw_json, {"note": "no openai call made"})
        news_html.write_text(render_news_html(project, news_obj), encoding="utf-8")
        return news_json, news_html, inputs_json, raw_json

    # latest event = max date (lexicographically works for YYYY-MM-DD)
    latest = sorted(key_events, key=lambda e: e["date"])[-1]

    # Build a stable event list for the model with ids
    compact_events = []
    for i, e in enumerate(key_events, start=1):
        compact_events.append(
            {
                "event_id": f"E{i}",
                "date": e["date"],
                "claim": e["claim"],
                "confidence": e.get("confidence", 0.0),
                "domain": e["evidence"]["domain"],
                "url": e["evidence"]["final_url"],
                "snippet": e["evidence"]["snippet"],
            }
        )

    system = (
        "You generate a real-estate 'stalled project news' object.\n"
        "Hard rule: you MUST NOT add facts that are not directly supported by the provided event snippets.\n"
        "Every timeline bullet, latest update line, and buyer implication MUST cite supporting_event_ids (one or more).\n"
        "If evidence is insufficient, say 'Insufficient evidence' and still cite what exists.\n"
        "Return ONLY valid JSON."
    )

    user = json.dumps(
        {
            "project": {
                "project_name": project.project_name,
                "city": project.city,
                "rera_id": project.rera_id,
            },
            "events": compact_events,
            "required_output_schema": {
                "headline": "string",
                "summary": "string (2-3 lines, concise)",
                "primary": {"date": "YYYY-MM-DD", "source": {"domain": "string", "url": "string"}, "supporting_event_ids": ["E1"]},
                "timeline": [{"date": "YYYY-MM-DD", "text": "string", "supporting_event_ids": ["E1", "E2"]}],
                "latestUpdate": {"date": "YYYY-MM-DD", "text": "string", "supporting_event_ids": ["E3"]},
                "buyerImplications": [{"text": "string", "supporting_event_ids": ["E1"]}],
                "sources": [{"domain": "string", "url": "string"}],
            },
        },
        ensure_ascii=False,
        indent=2,
    )

    llm = chat_completion_json(system=system, user=user, temperature=0.2, max_tokens=1100)

    # Basic validation: referenced ids must exist
    valid_ids = {e["event_id"] for e in compact_events}

    def _check_ids(where: str, ids: List[str]) -> None:
        for x in ids:
            if x not in valid_ids:
                raise RuntimeError(f"Invalid supporting_event_id '{x}' in {where}. Valid={sorted(list(valid_ids))}")

    # validate primary
    primary = llm.get("primary") or {}
    _check_ids("primary.supporting_event_ids", primary.get("supporting_event_ids") or [])

    # validate timeline
    for idx, t in enumerate(llm.get("timeline") or []):
        _check_ids(f"timeline[{idx}].supporting_event_ids", t.get("supporting_event_ids") or [])

    # validate latestUpdate
    lu = llm.get("latestUpdate") or {}
    _check_ids("latestUpdate.supporting_event_ids", lu.get("supporting_event_ids") or [])

    # validate buyerImplications
    for idx, b in enumerate(llm.get("buyerImplications") or []):
        _check_ids(f"buyerImplications[{idx}].supporting_event_ids", b.get("supporting_event_ids") or [])

    # Build final news object in your required schema
    sources = []
    seen = set()
    for e in compact_events:
        k = (e["domain"], e["url"])
        if k not in seen:
            seen.add(k)
            sources.append({"domain": e["domain"], "url": e["url"]})

    news_obj = {
        "headline": llm.get("headline"),
        "summary": llm.get("summary"),
        "dateAndSource": {
            "date": primary.get("date"),
            "source": primary.get("source"),
            "supportingEventIds": primary.get("supporting_event_ids"),
        },
        "timeline": [
            {
                "date": t.get("date"),
                "text": t.get("text"),
                "supportingEventIds": t.get("supporting_event_ids"),
            }
            for t in (llm.get("timeline") or [])
        ],
        "latestUpdate": {
            "date": (llm.get("latestUpdate") or {}).get("date"),
            "text": (llm.get("latestUpdate") or {}).get("text"),
            "supportingEventIds": (llm.get("latestUpdate") or {}).get("supporting_event_ids"),
        },
        "buyerImplications": [
            {
                "text": b.get("text"),
                "supportingEventIds": b.get("supporting_event_ids"),
            }
            for b in (llm.get("buyerImplications") or [])
        ],
        "sources": sources,  # references only, no backlinks
        "generatedAt": _utc_now_iso(),
        "validUntil": _valid_until_iso(7),
        "debug": {
            "eventIdMap": {e["event_id"]: {"date": e["date"], "domain": e["domain"], "url": e["url"]} for e in compact_events}
        },
    }

    news_json = run_dir / "news.json"
    news_html = run_dir / "news.html"
    inputs_json = run_dir / "news_inputs.json"
    raw_json = run_dir / "news_llm_raw.json"

    _store_json(inputs_json, {"project": project.model_dump(), "events": compact_events})
    _store_json(raw_json, llm)
    _store_json(news_json, news_obj)

    news_html.write_text(render_news_html(project, news_obj), encoding="utf-8")

    return news_json, news_html, inputs_json, raw_json


def render_news_html(project: ProjectInput, news: Dict[str, Any]) -> str:
    headline = news.get("headline") or ""
    summary = news.get("summary") or ""
    primary = news.get("dateAndSource") or {}
    timeline = news.get("timeline") or []
    latest = news.get("latestUpdate") or {}
    implications = news.get("buyerImplications") or []
    sources = news.get("sources") or []
    gen = news.get("generatedAt") or ""
    valid = news.get("validUntil") or ""

    def esc(s: str) -> str:
        return (
            s.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    def fmt_ids(ids: List[str]) -> str:
        if not ids:
            return ""
        return " (" + ", ".join([esc(x) for x in ids]) + ")"

    primary_date = primary.get("date")
    primary_source = primary.get("source") or {}
    primary_ids = primary.get("supportingEventIds") or primary.get("supporting_event_ids") or []

    html = []
    html.append("<!doctype html>")
    html.append("<html lang='en'>")
    html.append("<head>")
    html.append("<meta charset='utf-8'/>")
    html.append("<meta name='viewport' content='width=device-width, initial-scale=1'/>")
    html.append(f"<title>{esc(headline) or 'Stalled Project News'}</title>")
    html.append("<style>")
    html.append("body{font-family:Arial,Helvetica,sans-serif;margin:24px;line-height:1.45;color:#111}")
    html.append("h1{margin:0 0 8px 0;font-size:22px}")
    html.append(".meta{color:#555;font-size:13px;margin-bottom:14px}")
    html.append(".card{border:1px solid #e5e5e5;border-radius:10px;padding:14px;margin:14px 0}")
    html.append("ul{margin:8px 0 0 18px}")
    html.append("li{margin:6px 0}")
    html.append("code{background:#f6f6f6;padding:1px 6px;border-radius:6px}")
    html.append("</style>")
    html.append("</head>")
    html.append("<body>")

    html.append(f"<h1>{esc(headline)}</h1>")
    html.append(f"<div class='meta'><b>Project:</b> {esc(project.project_name)} | <b>City:</b> {esc(project.city)}"
                + (f" | <b>RERA:</b> {esc(project.rera_id)}" if project.rera_id else "")
                + "</div>")

    html.append("<div class='card'>")
    html.append("<b>2–3 line summary</b>")
    html.append(f"<p>{esc(summary)}</p>")
    html.append("</div>")

    html.append("<div class='card'>")
    html.append("<b>Date and Source (Primary)</b>")
    if primary_date and primary_source.get("domain") and primary_source.get("url"):
        html.append(f"<p><b>{esc(primary_date)}</b> — {esc(primary_source['domain'])} | <span style='color:#555'>{esc(primary_source['url'])}</span>{fmt_ids(primary_ids)}</p>")
    else:
        html.append("<p>Insufficient evidence.</p>")
    html.append("</div>")

    html.append("<div class='card'>")
    html.append("<b>Timeline of key events</b>")
    if timeline:
        html.append("<ul>")
        for t in timeline:
            html.append(f"<li><b>{esc(t.get('date') or '')}</b> — {esc(t.get('text') or '')}{fmt_ids(t.get('supportingEventIds') or [])}</li>")
        html.append("</ul>")
    else:
        html.append("<p>Insufficient evidence.</p>")
    html.append("</div>")

    html.append("<div class='card'>")
    html.append("<b>Latest update</b>")
    if latest.get("date") and latest.get("text"):
        html.append(f"<p><b>{esc(latest.get('date'))}</b> — {esc(latest.get('text'))}{fmt_ids(latest.get('supportingEventIds') or [])}</p>")
    else:
        html.append("<p>Insufficient evidence.</p>")
    html.append("</div>")

    html.append("<div class='card'>")
    html.append("<b>What it means for buyers</b>")
    if implications:
        html.append("<ul>")
        for b in implications:
            html.append(f"<li>{esc(b.get('text') or '')}{fmt_ids(b.get('supportingEventIds') or [])}</li>")
        html.append("</ul>")
    else:
        html.append("<p>Insufficient evidence.</p>")
    html.append("</div>")

    html.append("<div class='card'>")
    html.append("<b>Sources (references only)</b>")
    if sources:
        html.append("<ul>")
        for s in sources:
            html.append(f"<li>{esc(s.get('domain') or '')} — <span style='color:#555'>{esc(s.get('url') or '')}</span></li>")
        html.append("</ul>")
    else:
        html.append("<p>No sources.</p>")
    html.append("</div>")

    html.append(f"<div class='meta'>GeneratedAt: <code>{esc(gen)}</code> | ValidUntil: <code>{esc(valid)}</code></div>")
    html.append("</body></html>")
    return "\n".join(html)
