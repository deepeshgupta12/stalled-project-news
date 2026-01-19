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
    re.compile(r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+([0-3]?\d)(?:st|nd|rd|th)?,\s+((?:19|20)\d{2})\b", re.I),
    # dd Month yyyy
    re.compile(r"\b([0-3]?\d)\s+(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+((?:19|20)\d{2})\b", re.I),
]

KEYWORD_TAGS = {
    "rera": ["rera", "authority", "order", "registration", "complaint", "hearing", "adjudicating", "penalty", "revocation"],
    "court": ["court", "high court", "supreme court", "appeal", "petition", "writ", "judgment", "order"],
    "possession": ["possession", "handover", "delivery", "completion", "occupancy", "oc", "cc", "completion certificate", "occupancy certificate"],
    "finance": ["escrow", "bank", "loan", "fund", "payment", "refund", "interest", "compensation"],
    "construction": ["construction", "site", "work", "progress", "tower", "structure", "slab", "foundation", "inspection"],
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
    if any(k in s for k in ["order", "hearing", "directed", "authority", "rera", "penalty", "revocation"]):
        score += 0.25
    if any(k in s for k in ["dated", "date", "on ", "as on"]):
        score += 0.10
    if len(s) > 120:
        score += 0.10
    if any(k in s for k in ["alleged", "rumour", "rumor"]):
        score -= 0.10
    return max(0.0, min(0.99, score))


def _claim_from_snippet(snippet: str) -> str:
    # Try to produce a human-ish claim from snippet (bounded by evidence)
    s = re.sub(r"\s+", " ", snippet).strip()
    return s[:420] + ("…" if len(s) > 420 else "")


def _find_events_in_text(text: str) -> List[Tuple[str, str, float, List[str]]]:
    """
    Returns list of (iso_date, snippet, confidence, tags)
    Snippet must be present in normalized text (checked later).
    """
    norm_text = " ".join(text.split())
    events: List[Tuple[str, str, float, List[str]]] = []

    for pat in DATE_PATTERNS:
        for m in pat.finditer(norm_text):
            iso = _parse_date_from_match(m)
            if not iso:
                continue

            # Context window around the date mention
            start = max(0, m.start() - 220)
            end = min(len(norm_text), m.end() + 220)
            window = norm_text[start:end].strip()

            # Snippet: keep a compact window (try to cut at sentence boundaries)
            snippet = window
            # crude sentence cut
            if "." in window:
                parts = window.split(".")
                # take up to 2 sentence chunks
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


def load_evidence(evidence_path: Path) -> List[Dict[str, Any]]:
    """
    Compatibility loader:
    - Old format: a list[dict]
    - New format (Step 6E wide): {"counts": {...}, "docs": [ {doc_id, url, final_url, domain, snippet, text_path}, ... ]}
    Converts new format into the old per-doc dict shape used by the extractor.
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

            # Compute textChars safely (do not crash if missing)
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
    max_events_per_doc: int = 20,
) -> Tuple[List[TimelineEvent], List[TimelineEvent]]:
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

        found = _find_events_in_text(text)
        found = sorted(found, key=lambda x: (-x[2], x[0]))[:max_events_per_doc]

        for iso, snippet, conf, tags in found:
            if conf < min_confidence:
                continue

            # Validate snippet exists in normalized text
            if snippet not in " ".join(text.split()):
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

    # Dedupe: (date + normalized claim prefix)
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


def store_events(raw: List[TimelineEvent], deduped: List[TimelineEvent], run_dir: Path) -> Dict[str, str]:
    run_dir.mkdir(parents=True, exist_ok=True)

    raw_path = run_dir / "events_raw.json"
    dedup_path = run_dir / "events_deduped.json"
    timeline_path = run_dir / "timeline.json"

    store_timeline(raw, raw_path)
    store_timeline(deduped, dedup_path)
    store_timeline(deduped, timeline_path)

    return {
        "events_raw": str(raw_path),
        "events_deduped": str(dedup_path),
        "timeline": str(timeline_path),
    }
