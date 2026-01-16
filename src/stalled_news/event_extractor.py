from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .events import EvidenceRef, TimelineEvent


_MONTHS = {
    "jan": "01", "january": "01",
    "feb": "02", "february": "02",
    "mar": "03", "march": "03",
    "apr": "04", "april": "04",
    "may": "05",
    "jun": "06", "june": "06",
    "jul": "07", "july": "07",
    "aug": "08", "august": "08",
    "sep": "09", "sept": "09", "september": "09",
    "oct": "10", "october": "10",
    "nov": "11", "november": "11",
    "dec": "12", "december": "12",
}


DATE_PATTERNS: list[re.Pattern] = [
    # 27-Jun-2022 or 27-Jun-22
    re.compile(r"\b(\d{1,2})[-\s]([A-Za-z]{3,9})[-\s](\d{2,4})\b"),
    # 25.04.2022 or 25/04/2022
    re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{2,4})\b"),
    # 2022-06-27
    re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),
]

KEYWORDS = {
    "registration suspended": 1.0,
    "suspended": 0.8,
    "show-cause": 0.9,
    "show cause": 0.9,
    "rejection": 0.8,
    "adjourned": 0.6,
    "adjournment": 0.6,
    "hearing": 0.5,
    "order": 0.6,
    "certificate": 0.4,
    "notice": 0.4,
    "extension": 0.6,
    "revoked": 0.8,
    "penalty": 0.7,
    "complaint": 0.5,
}


def _normalize(s: str) -> str:
    return " ".join(s.lower().strip().split())


def _to_iso_date_from_match(m: re.Match) -> Optional[str]:
    g = m.groups()

    # YYYY-MM-DD
    if len(g) == 3 and len(g[0]) == 4 and g[0].isdigit():
        y, mo, d = g
        return f"{y}-{mo.zfill(2)}-{d.zfill(2)}"

    # DD-Mon-YYYY
    if len(g) == 3 and g[1].isalpha():
        d = g[0]
        mon = _MONTHS.get(g[1].lower())
        y = g[2]
        if not mon:
            return None
        if len(y) == 2:
            # assume 20xx for 2-digit years (good enough for RERA timelines)
            y = "20" + y
        return f"{y}-{mon}-{d.zfill(2)}"

    # DD.MM.YYYY
    if len(g) == 3 and g[1].isdigit():
        d, mo, y = g
        if len(y) == 2:
            y = "20" + y
        return f"{y}-{mo.zfill(2)}-{d.zfill(2)}"

    return None


def _best_keyword_score(text: str) -> Tuple[float, list[str]]:
    t = _normalize(text)
    score = 0.0
    tags: list[str] = []
    for k, w in KEYWORDS.items():
        if k in t:
            score = max(score, w)
            tags.append(k)
    # de-dupe tags but keep stable order-ish
    tags = list(dict.fromkeys(tags))
    return score, tags


def _extract_line_windows(text: str) -> List[str]:
    # Keep RERA table-like rows intact (many are pipe-separated)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines


def _find_events_in_text(text: str) -> List[Tuple[str, str, float, list[str]]]:
    """
    Returns list of (iso_date, snippet, confidence, tags)
    Snippet is a verbatim line/window containing the date.
    """
    lines = _extract_line_windows(text)
    results: List[Tuple[str, str, float, list[str]]] = []

    for ln in lines:
        for pat in DATE_PATTERNS:
            m = pat.search(ln)
            if not m:
                continue
            iso = _to_iso_date_from_match(m)
            if not iso:
                continue

            kw_score, tags = _best_keyword_score(ln)
            conf = 0.45 + 0.5 * kw_score  # 0.45..0.95
            conf = max(0.35, min(0.95, conf))

            # Keep snippet bounded but verbatim
            snippet = ln
            if len(snippet) > 420:
                snippet = snippet[:420].rstrip() + "…"

            results.append((iso, snippet, conf, tags))
    return results


def _claim_from_snippet(snippet: str) -> str:
    # Light cleaning, no new facts beyond snippet
    s = " ".join(snippet.replace("|", " ").split())
    if len(s) > 220:
        s = s[:220].rstrip() + "…"
    return s


def load_evidence(evidence_path: Path) -> List[Dict[str, Any]]:
    return json.loads(evidence_path.read_text(encoding="utf-8"))


def load_text(path_str: str) -> str:
    p = Path(path_str)
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")


def extract_events_from_evidence(
    evidence_path: Path,
    *,
    min_confidence: float = 0.55,
) -> Tuple[List[TimelineEvent], List[TimelineEvent]]:
    """
    Returns (raw_events, deduped_events)
    """
    ev = load_evidence(evidence_path)

    raw: List[TimelineEvent] = []

    for e in ev:
        if (e.get("textChars") or 0) <= 0:
            continue  # skip empty / needs OCR docs for now
        text = load_text(e["textPath"])
        if not text.strip():
            continue

        # Strict validation later uses this exact text
        found = _find_events_in_text(text)

        for iso, snippet, conf, tags in found:
            # Validate snippet exists in text (verbatim safety)
            if snippet.replace("…", "") not in text:
                # If truncated with ellipsis, validate prefix exists
                prefix = snippet.replace("…", "")
                if prefix and prefix not in text:
                    continue

            claim = _claim_from_snippet(snippet)

            if conf < min_confidence:
                continue

            raw.append(
                TimelineEvent(
                    date=iso,
                    claim=claim,
                    confidence=conf,
                    tags=tags,
                    evidence=EvidenceRef(
                        doc_id=e["id"],
                        url=e["url"],
                        final_url=e["finalUrl"],
                        domain=e["domain"],
                        snippet=snippet,
                        text_path=e["textPath"],
                    ),
                )
            )

    # Dedup: date + normalized snippet
    seen = set()
    deduped: List[TimelineEvent] = []
    for item in sorted(raw, key=lambda x: (x.date, -x.confidence)):
        key = (item.date, _normalize(item.evidence.snippet))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    # Sort timeline ascending
    deduped = sorted(deduped, key=lambda x: x.date)
    return raw, deduped


def store_events(
    evidence_path: Path,
    raw: List[TimelineEvent],
    deduped: List[TimelineEvent],
) -> Tuple[Path, Path, Path]:
    run_dir = evidence_path.parent
    raw_path = run_dir / "events_raw.json"
    deduped_path = run_dir / "events_deduped.json"
    timeline_path = run_dir / "timeline.json"

    raw_path.write_text(
        json.dumps([r.model_dump() for r in raw], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    deduped_path.write_text(
        json.dumps([d.model_dump() for d in deduped], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    timeline_path.write_text(
        json.dumps(
            [{"date": d.date, "claim": d.claim, "source": {"domain": d.evidence.domain, "url": d.evidence.final_url}} for d in deduped],
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    return raw_path, deduped_path, timeline_path
