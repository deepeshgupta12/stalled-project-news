from __future__ import annotations

import json
import re
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
    re.compile(r"\b(\d{1,2})[-\s]([A-Za-z]{3,9})[-\s](\d{2,4})\b"),     # 27-Jun-2022
    re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{2,4})\b"),            # 25.04.2022
    re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),                        # 2022-06-27
]

KEYWORDS = {
    "registration suspended": 1.0,
    "registra": 0.3,  # catch "registration" broadly
    "suspended": 0.8,
    "show-cause": 0.9,
    "show cause": 0.9,
    "rejection": 0.8,
    "adjourned": 0.6,
    "adjournment": 0.6,
    "hearing": 0.5,
    "order": 0.6,
    "notice": 0.5,
    "extension": 0.6,
    "revoked": 0.8,
    "penalty": 0.7,
    "complaint": 0.5,
    "non-compliance": 0.7,
    "default": 0.5,
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
    tags = list(dict.fromkeys(tags))
    return score, tags


def _context_window(text: str, start: int, end: int, *, window: int = 320) -> str:
    a = max(0, start - window)
    b = min(len(text), end + window)
    snippet = text[a:b].strip()

    # Collapse whitespace but keep verbatim characters (no paraphrase)
    # NOTE: we must later validate this exact snippet exists in text.
    snippet = " ".join(snippet.split())
    if len(snippet) > 520:
        snippet = snippet[:520].rstrip() + "…"
    return snippet


def _find_events_in_text(text: str) -> List[Tuple[str, str, float, list[str]]]:
    """
    Returns list of (iso_date, snippet, confidence, tags)
    Snippet is a verbatim context window around the date match (validated later).
    """
    results: List[Tuple[str, str, float, list[str]]] = []

    for pat in DATE_PATTERNS:
        for m in pat.finditer(text):
            iso = _to_iso_date_from_match(m)
            if not iso:
                continue
            snippet = _context_window(text, m.start(), m.end())
            kw_score, tags = _best_keyword_score(snippet)

            conf = 0.45 + 0.5 * kw_score  # 0.45..0.95
            conf = max(0.35, min(0.95, conf))
            results.append((iso, snippet, conf, tags))

    return results


def _claim_from_snippet(snippet: str) -> str:
    s = " ".join(snippet.replace("|", " ").split())
    if len(s) > 240:
        s = s[:240].rstrip() + "…"
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
    max_events_per_doc: int = 20,
) -> Tuple[List[TimelineEvent], List[TimelineEvent]]:
    ev = load_evidence(evidence_path)
    raw: List[TimelineEvent] = []

    for e in ev:
        if (e.get("textChars") or 0) <= 0:
            continue
        text = load_text(e["textPath"])
        if not text.strip():
            continue

        found = _find_events_in_text(text)

        # Keep best events per doc
        found = sorted(found, key=lambda x: (-x[2], x[0]))[:max_events_per_doc]

        for iso, snippet, conf, tags in found:
            if conf < min_confidence:
                continue

            # Validate snippet exists in text.
            # If snippet ends with ellipsis, validate prefix exists.
            if snippet.endswith("…"):
                prefix = snippet[:-1].strip()
                if prefix and prefix not in " ".join(text.split()):
                    continue
            else:
                # We normalized whitespace in snippet, so validate on normalized text too.
                if snippet not in " ".join(text.split()):
                    continue

            claim = _claim_from_snippet(snippet)

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

    # Better dedupe: date + normalized claim (first ~160 chars)
    seen = set()
    deduped: List[TimelineEvent] = []
    for item in sorted(raw, key=lambda x: (x.date, -x.confidence)):
        core = _normalize(item.claim)[:160]
        key = (item.date, core)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    deduped = sorted(deduped, key=lambda x: (x.date, -x.confidence))
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
