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
    # dd.mm.yyyy or dd-mm-yyyy or dd/mm/yyyy
    re.compile(r"\b([0-3]?\d)[./-]([01]?\d)[./-]((?:19|20)\d{2})\b"),
    # yyyy-mm-dd
    re.compile(r"\b((?:19|20)\d{2})-([01]\d)-([0-3]\d)\b"),
    # Month dd, yyyy
    re.compile(
        r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+([0-3]?\d)(?:st|nd|rd|th)?,\s+((?:19|20)\d{2})\b",
        re.I,
    ),
    # dd Month yyyy
    re.compile(
        r"\b([0-3]?\d)\s+(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+((?:19|20)\d{2})\b",
        re.I,
    ),
]

KEYWORD_TAGS = {
    "rera": ["rera", "authority", "order", "registration", "complaint", "hearing", "adjudicating", "penalty", "revocation", "suspended", "suspension"],
    "court": ["court", "high court", "supreme court", "appeal", "petition", "writ", "judgment", "order"],
    "possession": ["possession", "handover", "delivery", "completion", "occupancy", "oc", "cc", "completion certificate", "occupancy certificate"],
    "finance": ["escrow", "bank", "loan", "fund", "payment", "refund", "interest", "compensation", "bank guarantee", "bg"],
    "construction": ["construction", "site", "work", "progress", "tower", "structure", "slab", "foundation", "inspection"],
    "news": ["reported", "announced", "said", "according to", "sources", "article", "news"],
}


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def _alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


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


def _confidence(snippet: str, *, domain: str = "", doc_relevant: bool = False) -> float:
    s = _normalize(snippet)
    score = 0.30

    # Strong signals
    if any(k in s for k in ["hearing", "adjourned", "directed", "authority", "rera", "registration", "suspended", "suspension", "penalty", "revocation"]):
        score += 0.30

    # Date framing language
    if any(k in s for k in ["dated", "submission date", "dak id", "on ", "as on", "preponed", "within one week", "within three days", "compliance"]):
        score += 0.10

    # Longer context tends to be more informative
    if len(s) > 140:
        score += 0.10

    # Domain trust bump
    d = (domain or "").lower()
    if d.endswith("gov.in") or d.endswith("nic.in"):
        score += 0.10

    # Relevance bump (doc-level)
    if doc_relevant:
        score += 0.10

    # Uncertainty penalty
    if any(k in s for k in ["alleged", "rumour", "rumor", "maybe", "unconfirmed"]):
        score -= 0.15

    return max(0.0, min(0.99, score))


def _claim_from_snippet(snippet: str) -> str:
    s = re.sub(r"\s+", " ", snippet).strip()
    return s[:420] + ("…" if len(s) > 420 else "")


def _find_events_in_text(text: str) -> List[Tuple[str, str]]:
    """
    Returns list of (iso_date, snippet_window)
    """
    norm_text = " ".join(text.split())
    events: List[Tuple[str, str]] = []

    for pat in DATE_PATTERNS:
        for m in pat.finditer(norm_text):
            iso = _parse_date_from_match(m)
            if not iso:
                continue

            start = max(0, m.start() - 240)
            end = min(len(norm_text), m.end() + 240)
            window = norm_text[start:end].strip()

            # Try to cut at sentence boundaries but keep enough signal
            snippet = window
            if "." in window:
                parts = [p.strip() for p in window.split(".") if p.strip()]
                snippet = ". ".join(parts[:2]).strip()
                if len(snippet) < 60 and len(parts) > 2:
                    snippet = ". ".join(parts[:3]).strip()

            snippet = snippet[:560].strip()
            if len(snippet) < 30:
                continue

            events.append((iso, snippet))

    return events


def load_text(path_str: str) -> str:
    p = Path(path_str)
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")


def _extract_project_context(evidence_path: Path) -> Dict[str, Any]:
    """
    evidence.json (wide format) contains:
      { "project": {project_name, city, rera_id}, "docs": [...] }
    If missing, returns empty context.
    """
    try:
        raw = json.loads(evidence_path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    if not isinstance(raw, dict):
        return {}

    proj = raw.get("project") or {}
    if not isinstance(proj, dict):
        proj = {}

    project_name = (proj.get("project_name") or proj.get("projectName") or "").strip()
    city = (proj.get("city") or "").strip()
    rera_id = (proj.get("rera_id") or proj.get("reraId") or "").strip()

    tokens = [t for t in re.findall(r"[a-z0-9]+", project_name.lower()) if len(t) >= 3]

    return {
        "project_name": project_name,
        "city": city,
        "rera_id": rera_id,
        "tokens": tokens,
        "rera_alnum": _alnum(rera_id),
        "project_alnum": _alnum(project_name),
        "city_alnum": _alnum(city),
    }


def _doc_is_relevant(text: str, ctx: Dict[str, Any]) -> bool:
    """
    Document-level relevance gate:
    Keep ONLY docs that mention:
      - rera_id (best), OR
      - project name (full or token hits)
    This prevents random nic.in pages from polluting timeline.
    """
    if not ctx:
        # If no context available, do not filter (fallback)
        return True

    norm = _normalize(text)
    norm_alnum = _alnum(text)

    rera = (ctx.get("rera_id") or "").lower()
    rera_alnum = ctx.get("rera_alnum") or ""
    if rera and (rera in norm or (rera_alnum and rera_alnum in norm_alnum)):
        return True

    pn = (ctx.get("project_name") or "").lower()
    pn_alnum = ctx.get("project_alnum") or ""
    if pn and (pn in norm or (pn_alnum and pn_alnum in norm_alnum)):
        return True

    toks: List[str] = ctx.get("tokens") or []
    if toks:
        hits = sum(1 for t in toks if t in norm)
        # require at least 2 token hits for multi-word names like "Zara Roma"
        if hits >= min(2, len(toks)):
            return True

    return False


def load_evidence(evidence_path: Path) -> List[Dict[str, Any]]:
    """
    Compatibility loader:
    - Old format: a list[dict]
    - New format: {"counts": {...}, "docs": [ {doc_id, url, final_url, domain, snippet, text_path}, ... ]}
    Converts new format into the old per-doc dict shape used by extractor.
    """
    data = json.loads(evidence_path.read_text(encoding="utf-8"))

    if isinstance(data, list):
        return data

    if isinstance(data, dict) and isinstance(data.get("docs"), list):
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
        return out

    raise ValueError(f"Unsupported evidence.json format at: {evidence_path}")


def extract_events_from_evidence(
    evidence_path: Path,
    *,
    min_confidence: float = 0.55,
    max_events_per_doc: int = 40,
) -> Tuple[List[TimelineEvent], List[TimelineEvent]]:
    ctx = _extract_project_context(evidence_path)
    ev = load_evidence(evidence_path)

    raw: List[TimelineEvent] = []

    for e in ev:
        if not isinstance(e, dict):
            continue
        if (e.get("textChars") or 0) <= 0:
            continue

        text = load_text(e.get("textPath", ""))
        if not text.strip():
            continue

        # DOCUMENT-LEVEL RELEVANCE FILTER (this is the key fix)
        doc_rel = _doc_is_relevant(text, ctx)
        if not doc_rel:
            continue

        dom = str(e.get("domain") or "")

        found = _find_events_in_text(text)

        # Score & rank inside the doc (keep top N)
        scored: List[Tuple[float, str, str, List[str]]] = []
        for iso, snippet in found:
            tags = _extract_tags(snippet)
            conf = _confidence(snippet, domain=dom, doc_relevant=doc_rel)
            scored.append((conf, iso, snippet, tags))

        scored = sorted(scored, key=lambda x: (-x[0], x[1]))[:max_events_per_doc]

        norm_text = " ".join(text.split())
        for conf, iso, snippet, tags in scored:
            if conf < min_confidence:
                continue

            # Safety: snippet must exist in extracted text (evidence-bounded)
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

    # Dedupe:
    # keep multiple events per date if claim differs meaningfully, but collapse near-identical repeats
    seen = set()
    deduped: List[TimelineEvent] = []
    for item in sorted(raw, key=lambda x: (x.date, -x.confidence)):
        core = _normalize(item.claim)[:180]
        # include doc_id to avoid collapsing the same event echoed across multiple sources
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
    # argparse / caller safety: evidence_path might be a list
    if isinstance(evidence_path, (list, tuple)):
        if not evidence_path:
            raise ValueError("store_events: evidence_path is an empty list/tuple")
        evidence_path = evidence_path[0]

    ep = Path(evidence_path)

    # If user passes a directory, use it; else use parent of evidence.json
    run_dir = ep if ep.is_dir() else ep.parent
    run_dir.mkdir(parents=True, exist_ok=True)

    raw_path = run_dir / "events_raw.json"
    dedup_path = run_dir / "events_deduped.json"
    timeline_path = run_dir / "timeline.json"

    store_timeline(raw, raw_path)
    store_timeline(deduped, dedup_path)
    store_timeline(deduped, timeline_path)

    return str(raw_path), str(dedup_path), str(timeline_path)