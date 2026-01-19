import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import dateparser


@dataclass
class EvidenceRef:
    doc_id: str
    url: str
    final_url: str
    domain: str
    snippet: str
    text_path: str


@dataclass
class TimelineEvent:
    date: str  # ISO yyyy-mm-dd
    claim: str
    confidence: float
    tags: List[str]
    evidence: EvidenceRef


DATE_PATTERNS = [
    re.compile(r"\b([0-3]?\d)[./-]([01]?\d)[./-]((?:19|20)\d{2})\b"),
    re.compile(r"\b((?:19|20)\d{2})-([01]\d)-([0-3]\d)\b"),
    re.compile(
        r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+([0-3]?\d)(?:st|nd|rd|th)?,\s+((?:19|20)\d{2})\b",
        re.I,
    ),
    re.compile(
        r"\b([0-3]?\d)\s+(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+((?:19|20)\d{2})\b",
        re.I,
    ),
]

KEYWORD_TAGS = {
    "rera": ["rera", "authority", "order", "registration", "complaint", "hearing", "adjudicating", "penalty", "revocation", "coram", "dak id"],
    "court": ["court", "high court", "supreme court", "appeal", "petition", "writ", "judgment", "order"],
    "possession": ["possession", "handover", "delivery", "completion", "occupancy", "oc", "cc", "completion certificate", "occupancy certificate"],
    "finance": ["escrow", "bank", "loan", "fund", "payment", "refund", "interest", "compensation", "bank guarantee"],
    "construction": ["construction", "site", "work", "progress", "tower", "structure", "slab", "foundation", "inspection", "qpr"],
    "news": ["reported", "announced", "said", "according to", "sources", "article", "news"],
}


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def _to_iso(d: date) -> str:
    return d.isoformat()


def _parse_date_from_match(m: re.Match) -> Optional[str]:
    txt = m.group(0)
    dt = dateparser.parse(
        txt,
        settings={
            "PREFER_DAY_OF_MONTH": "first",
            "PREFER_DATES_FROM": "past",
            "DATE_ORDER": "DMY",
            "STRICT_PARSING": False,
        },
    )
    if not dt:
        return None
    return _to_iso(dt.date())


def _extract_tags(snippet: str) -> List[str]:
    s = _normalize(snippet)
    tags: List[str] = []
    for tag, kws in KEYWORD_TAGS.items():
        for kw in kws:
            if kw in s:
                tags.append(tag)
                break
    return tags or ["general"]


def _confidence(snippet: str) -> float:
    s = _normalize(snippet)
    score = 0.35
    if any(k in s for k in ["order", "hearing", "directed", "authority", "rera", "penalty", "revocation", "registration suspended", "suspended"]):
        score += 0.25
    if any(k in s for k in ["dated", "date", "submission date", "dak id", "on ", "as on"]):
        score += 0.10
    if len(s) > 120:
        score += 0.10
    if any(k in s for k in ["alleged", "rumour", "rumor"]):
        score -= 0.10
    return max(0.0, min(0.99, score))


def _claim_from_snippet(snippet: str) -> str:
    s = re.sub(r"\s+", " ", snippet).strip()
    return s[:420] + ("…" if len(s) > 420 else "")


def _find_events_in_text(text: str) -> List[Tuple[str, str, float, List[str]]]:
    norm_text = " ".join(text.split())
    events: List[Tuple[str, str, float, List[str]]] = []

    for pat in DATE_PATTERNS:
        for m in pat.finditer(norm_text):
            iso = _parse_date_from_match(m)
            if not iso:
                continue

            start = max(0, m.start() - 220)
            end = min(len(norm_text), m.end() + 220)
            window = norm_text[start:end].strip()

            snippet = window
            if "." in window:
                parts = window.split(".")
                snippet = ".".join(parts[:2]).strip()
                if len(snippet) < 40 and len(parts) > 2:
                    snippet = ".".join(parts[:3]).strip()

            snippet = snippet[:520].strip()
            if len(snippet) < 30:
                continue

            tags = _extract_tags(snippet)
            conf = _confidence(snippet)
            events.append((iso, snippet, conf, tags))

    return events


def load_text(path_str: str) -> str:
    p = Path(path_str)
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")


def _project_tokens(project_name: str) -> List[str]:
    pn = _normalize(project_name)
    toks = [t for t in re.split(r"[^a-z0-9]+", pn) if t]
    # drop too-short junk tokens
    return [t for t in toks if len(t) >= 3]


def _doc_relevance_ok(*, blob: str, project_name: str, city: str, rera_id: Optional[str]) -> bool:
    """
    STRICT relevance gate to prevent unrelated nic.in PDFs from generating timelines.
    Pass if:
      - rera_id present in blob, OR
      - full project_name present, OR
      - (>=2 project tokens present AND city present)
    """
    b = _normalize(blob)

    if rera_id:
        rid = _normalize(rera_id)
        # handle slash/space variations
        rid2 = rid.replace("/", " ").replace("-", " ")
        if rid in b or rid2 in b:
            return True

    pn = _normalize(project_name)
    if pn and pn in b:
        return True

    toks = _project_tokens(project_name)
    tok_hits = sum(1 for t in set(toks) if t in b)
    c = _normalize(city)

    if tok_hits >= 2 and (not c or c in b):
        return True

    return False


def load_evidence_bundle(evidence_path: Path) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Returns (project_meta, docs_list) in the "old dict-per-doc" shape:
      { id, url, finalUrl, domain, snippet, textPath, textChars, needsOcr }
    Accepts:
      - Old format: list[dict]
      - New format: {"project": {...}, "docs": [ {doc_id,url,final_url,domain,snippet,text_path}, ... ]}
    """
    data = json.loads(evidence_path.read_text(encoding="utf-8"))

    # Old format
    if isinstance(data, list):
        return ({}, data)

    # New format
    if isinstance(data, dict) and isinstance(data.get("docs"), list):
        project = data.get("project") or {}
        out: List[Dict[str, Any]] = []
        for d in data["docs"]:
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
                    "needsOcr": False,
                }
            )
        return (project, out)

    raise ValueError(f"Unsupported evidence.json format at: {evidence_path}")


def extract_events_from_evidence(
    evidence_path: Path,
    *,
    min_confidence: float = 0.55,
    max_events_per_doc: int = 12,
) -> Tuple[List[TimelineEvent], List[TimelineEvent]]:
    project_meta, ev = load_evidence_bundle(evidence_path)

    project_name = str(project_meta.get("project_name") or project_meta.get("projectName") or "").strip()
    city = str(project_meta.get("city") or "").strip()
    rera_id = project_meta.get("rera_id") or project_meta.get("reraId") or None
    rera_id = str(rera_id).strip() if rera_id else None

    raw: List[TimelineEvent] = []

    for e in ev:
        if not isinstance(e, dict):
            continue
        if (e.get("textChars") or 0) <= 0:
            continue

        snippet_hint = str(e.get("snippet") or "")
        text = load_text(str(e.get("textPath") or ""))
        if not text.strip():
            continue

        # -------- STRICT DOC-LEVEL RELEVANCE GATE ----------
        # If we don't have project metadata (older runs), skip the gate.
        if project_name and city:
            blob = f"{snippet_hint}\n{text[:5000]}"
            if not _doc_relevance_ok(blob=blob, project_name=project_name, city=city, rera_id=rera_id):
                continue

        found = _find_events_in_text(text)
        found = sorted(found, key=lambda x: (-x[2], x[0]))[:max_events_per_doc]

        norm_text = " ".join(text.split())
        for iso, snippet, conf, tags in found:
            if conf < min_confidence:
                continue

            # snippet must be present in normalized text
            if snippet not in norm_text:
                continue

            raw.append(
                TimelineEvent(
                    date=iso,
                    claim=_claim_from_snippet(snippet),
                    confidence=conf,
                    tags=tags,
                    evidence=EvidenceRef(
                        doc_id=str(e.get("id") or ""),
                        url=str(e.get("url") or ""),
                        final_url=str(e.get("finalUrl") or e.get("url") or ""),
                        domain=str(e.get("domain") or ""),
                        snippet=snippet,
                        text_path=str(e.get("textPath") or ""),
                    ),
                )
            )

    # Dedupe: (date + normalized claim prefix + doc_id)
    seen = set()
    deduped: List[TimelineEvent] = []
    for item in sorted(raw, key=lambda x: (x.date, -x.confidence)):
        core = _normalize(item.claim)[:160]
        key = (item.date, core, item.evidence.doc_id)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    deduped = sorted(deduped, key=lambda x: (x.date, -x.confidence))
    return raw, deduped


def store_timeline(events: List[TimelineEvent], out_path: Path) -> None:
    payload = []
    for e in events:
        payload.append(
            {
                "date": e.date,
                "claim": e.claim,
                "confidence": e.confidence,
                "tags": e.tags,
                "source": {
                    "domain": e.evidence.domain,
                    "url": e.evidence.url,
                    "final_url": e.evidence.final_url,
                    "doc_id": e.evidence.doc_id,
                    "text_path": e.evidence.text_path,
                    "snippet": e.evidence.snippet,
                },
            }
        )
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def store_events(evidence_path, raw, deduped):
    """
    Writes:
      - events_raw.json
      - events_deduped.json
      - timeline.json

    Accepts evidence_path as:
      - str / Path to evidence.json
      - OR a list/tuple containing that path (argparse nargs can cause this)
    """
    if isinstance(evidence_path, (list, tuple)):
        if not evidence_path:
            raise ValueError("store_events: evidence_path is an empty list/tuple")
        evidence_path = evidence_path[0]

    ep = Path(evidence_path)
    run_dir = ep if ep.is_dir() else ep.parent
    run_dir.mkdir(parents=True, exist_ok=True)

    raw_path = run_dir / "events_raw.json"
    dedup_path = run_dir / "events_deduped.json"
    timeline_path = run_dir / "timeline.json"

    store_timeline(raw, raw_path)
    store_timeline(deduped, dedup_path)
    store_timeline(deduped, timeline_path)

    return str(raw_path), str(dedup_path), str(timeline_path)