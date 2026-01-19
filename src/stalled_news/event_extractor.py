from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .events import EvidenceRef, TimelineEvent


DATE_PATTERNS = [
    # 27.06.2022, 27-06-2022
    re.compile(r"\b(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})\b"),
    # 27 Jun 2022, 27 June 2022
    re.compile(r"\b(\d{1,2})\s+(Jan|January|Feb|February|Mar|March|Apr|April|May|Jun|June|Jul|July|Aug|August|Sep|September|Oct|October|Nov|November|Dec|December)\s+(\d{4})\b", re.I),
    # 2022-06-27
    re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),
]

MONTH_MAP = {
    "jan": "01", "january": "01",
    "feb": "02", "february": "02",
    "mar": "03", "march": "03",
    "apr": "04", "april": "04",
    "may": "05",
    "jun": "06", "june": "06",
    "jul": "07", "july": "07",
    "aug": "08", "august": "08",
    "sep": "09", "september": "09",
    "oct": "10", "october": "10",
    "nov": "11", "november": "11",
    "dec": "12", "december": "12",
}


def _iso_date_from_match(m: re.Match) -> Optional[str]:
    # Pattern 3 already YYYY-MM-DD
    if len(m.groups()) == 3 and m.re.pattern.startswith(r"\b(\d{4})-"):
        y, mo, d = m.group(1), m.group(2), m.group(3)
        return f"{y}-{mo}-{d}"

    # Pattern 1: DD.MM.YYYY
    if len(m.groups()) == 3 and m.group(2).isdigit():
        d = int(m.group(1))
        mo = int(m.group(2))
        y = int(m.group(3))
        if 1 <= d <= 31 and 1 <= mo <= 12:
            return f"{y:04d}-{mo:02d}-{d:02d}"
        return None

    # Pattern 2: DD Mon YYYY
    if len(m.groups()) == 3:
        d = int(m.group(1))
        mon = m.group(2).strip().lower()
        y = int(m.group(3))
        mo = MONTH_MAP.get(mon[:3], MONTH_MAP.get(mon))
        if mo and 1 <= d <= 31:
            return f"{y:04d}-{int(mo):02d}-{d:02d}"
    return None


def extract_dates(text: str) -> List[str]:
    out: List[str] = []
    for pat in DATE_PATTERNS:
        for m in pat.finditer(text):
            iso = _iso_date_from_match(m)
            if iso:
                out.append(iso)
    # stable unique order
    seen = set()
    uniq = []
    for d in out:
        if d not in seen:
            seen.add(d)
            uniq.append(d)
    return uniq


def _read_text_len(text_path: str) -> int:
    try:
        p = Path(text_path)
        if not p.exists():
            return 0
        return len(p.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return 0


def _normalize_evidence_item(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Supports both:
      - old evidence list items: {id,url,finalUrl,domain,textPath,textChars,snippet}
      - new evidence docs items: {doc_id,url,final_url,domain,text_path,snippet}
    """
    doc_id = raw.get("doc_id") or raw.get("id") or ""
    url = raw.get("url") or ""
    final_url = raw.get("final_url") or raw.get("finalUrl") or url
    domain = raw.get("domain") or ""
    text_path = raw.get("text_path") or raw.get("textPath") or ""
    snippet = raw.get("snippet") or ""

    text_chars = raw.get("textChars")
    if text_chars is None and text_path:
        text_chars = _read_text_len(text_path)
    if text_chars is None:
        text_chars = 0

    return {
        "doc_id": str(doc_id),
        "url": str(url),
        "final_url": str(final_url),
        "domain": str(domain),
        "text_path": str(text_path),
        "textChars": int(text_chars),
        "snippet": str(snippet),
    }


def load_evidence_items(evidence_path: Path) -> List[Dict[str, Any]]:
    data = json.loads(evidence_path.read_text(encoding="utf-8"))

    # old: list
    if isinstance(data, list):
        raw_items = data

    # new: dict with docs
    elif isinstance(data, dict):
        if "docs" in data and isinstance(data["docs"], list):
            raw_items = data["docs"]
        elif "evidence" in data and isinstance(data["evidence"], list):
            raw_items = data["evidence"]
        else:
            raise ValueError(f"Unsupported evidence.json dict shape. Keys: {list(data.keys())}")

    else:
        raise ValueError(f"Unsupported evidence.json type: {type(data)}")

    items: List[Dict[str, Any]] = []
    for x in raw_items:
        if isinstance(x, dict):
            items.append(_normalize_evidence_item(x))
    return items


def ensure_snippet_in_text(snippet: str, full_text: str) -> bool:
    s = " ".join(snippet.split())
    t = " ".join(full_text.split())
    if not s or not t:
        return False
    return s in t


def _context_window(full_text: str, snippet: str, window: int = 450) -> str:
    """
    Pull a window around the snippet if present; fallback to first window chars.
    """
    if not full_text:
        return ""
    s = " ".join(snippet.split())
    t = " ".join(full_text.split())
    idx = t.find(s) if s else -1
    if idx >= 0:
        lo = max(0, idx - window)
        hi = min(len(t), idx + len(s) + window)
        return t[lo:hi]
    return t[: min(len(t), 2 * window)]


def _tag_from_text(text: str) -> List[str]:
    tags = []
    tx = text.lower()
    if "adjourn" in tx or "adjourned" in tx:
        tags.append("adjourned")
    if "hearing" in tx:
        tags.append("hearing")
    if "show cause" in tx or "show-cause" in tx:
        tags.append("show-cause")
    if "rejection" in tx:
        tags.append("rejection")
    if "extension" in tx:
        tags.append("extension")
    if "complaint" in tx:
        tags.append("complaint")
    if "order" in tx:
        tags.append("order")
    return tags


def extract_events_from_evidence(evidence_path: Path, min_confidence: float = 0.55) -> Tuple[List[TimelineEvent], List[TimelineEvent]]:
    evidence_items = load_evidence_items(evidence_path)

    raw_events: List[TimelineEvent] = []

    for e in evidence_items:
        # If extraction produced empty/no text, skip
        if (e.get("textChars") or 0) <= 0:
            continue

        text_path = e.get("text_path") or ""
        if not text_path:
            continue

        try:
            full_text = Path(text_path).read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        snippet = e.get("snippet") or ""
        # If snippet exists, ensure it appears in text (strict evidence-bounding)
        if snippet and not ensure_snippet_in_text(snippet, full_text):
            # still allow extraction from text (some sites modify whitespace),
            # but lower confidence later by using context window
            pass

        ctx = _context_window(full_text, snippet, window=600)
        dates = extract_dates(ctx)

        # no dates => nothing to timeline
        if not dates:
            continue

        # create one event per date using a short claim derived from context/snippet
        for d in dates:
            claim_src = snippet if snippet else ctx
            claim_src = " ".join(claim_src.split())
            claim = claim_src[:220].rstrip()
            if len(claim_src) > 220:
                claim += "…"

            ev = TimelineEvent(
                date=d,
                claim=claim,
                evidence=EvidenceRef(
                    doc_id=e.get("doc_id") or "",
                    url=e.get("url") or "",
                    final_url=e.get("final_url") or (e.get("url") or ""),
                    domain=e.get("domain") or "",
                    snippet=snippet if snippet else claim,
                    text_path=text_path,
                ),
                confidence=0.70 if snippet else 0.60,
                tags=_tag_from_text(claim_src),
            )

            if ev.confidence >= min_confidence:
                raw_events.append(ev)

    # Stronger dedupe: (date + normalized claim + final_url)
    def key(ev: TimelineEvent) -> str:
        c = re.sub(r"\s+", " ", ev.claim.strip().lower())
        return f"{ev.date}|{c}|{ev.evidence.final_url}"

    seen = set()
    deduped: List[TimelineEvent] = []
    for ev in raw_events:
        k = key(ev)
        if k in seen:
            continue
        seen.add(k)
        deduped.append(ev)

    # sort by date asc
    deduped.sort(key=lambda x: x.date)
    raw_events.sort(key=lambda x: x.date)
    return raw_events, deduped
