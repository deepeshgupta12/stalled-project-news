import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
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
    "rera": [
        "rera",
        "authority",
        "order",
        "registration",
        "complaint",
        "hearing",
        "adjudicating",
        "penalty",
        "revocation",
        "suspended",
    ],
    "court": ["court", "high court", "supreme court", "appeal", "petition", "writ", "judgment", "order"],
    "possession": [
        "possession",
        "handover",
        "delivery",
        "completion",
        "occupancy",
        "oc",
        "cc",
        "completion certificate",
        "occupancy certificate",
    ],
    "finance": ["escrow", "bank", "loan", "fund", "payment", "refund", "interest", "compensation", "guarantee"],
    "construction": [
        "construction",
        "site",
        "work",
        "progress",
        "tower",
        "structure",
        "slab",
        "foundation",
        "inspection",
    ],
    "news": ["reported", "announced", "said", "according to", "sources", "article", "news"],
}

STOPWORDS = {
    "the",
    "and",
    "of",
    "in",
    "at",
    "to",
    "for",
    "by",
    "with",
    "on",
    "a",
    "an",
    "phase",
    "sector",
    "tower",
    "towers",
    "block",
    "blocks",
    "residency",
    "residence",
    "apartments",
    "apartment",
    "project",
    "gurgaon",  # keep city out of project token set to avoid double counting
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
    if any(k in s for k in ["order", "hearing", "directed", "authority", "rera", "penalty", "revocation", "suspended"]):
        score += 0.25
    if any(k in s for k in ["dated", "date", "on ", "as on", "adjourned", "come up"]):
        score += 0.10
    if len(s) > 120:
        score += 0.10
    if any(k in s for k in ["alleged", "rumour", "rumor"]):
        score -= 0.10
    return max(0.0, min(0.99, score))


def _claim_from_snippet(snippet: str) -> str:
    s = re.sub(r"\s+", " ", snippet).strip()
    return s[:420] + ("…" if len(s) > 420 else "")


RERA_REGEX = re.compile(r"\b([A-Z]{2,6})\s*[/\-]\s*(\d{1,6})\s*[/\-]\s*(\d{1,6})\s*[/\-]\s*((?:19|20)\d{2})\s*[/\-]\s*(\d{1,6})\b")


def _extract_rera_ids(text: str) -> List[str]:
    out = []
    for m in RERA_REGEX.finditer(text.upper()):
        out.append(f"{m.group(1)}/{m.group(2)}/{m.group(3)}/{m.group(4)}/{m.group(5)}")
    return out


def _tokenize_project(project_name: Optional[str]) -> List[str]:
    if not project_name:
        return []
    toks = [t for t in re.split(r"[^a-z0-9]+", _normalize(project_name)) if t]
    toks = [t for t in toks if len(t) >= 3 and t not in STOPWORDS]
    # de-dupe while preserving order
    seen = set()
    out = []
    for t in toks:
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _count_token_hits(hay: str, tokens: List[str]) -> int:
    if not hay or not tokens:
        return 0
    h = _normalize(hay)
    return sum(1 for t in tokens if t in h)


def _rera_pattern(rera_id: Optional[str]) -> Optional[re.Pattern]:
    if not rera_id:
        return None
    rid = rera_id.strip().upper()
    # Make it tolerant to spaces, hyphens, slashes
    esc = re.escape(rid)
    esc = esc.replace("/", r"[/\\-]\s*")
    return re.compile(esc)


def _is_doc_relevant(
    *,
    text: str,
    snippet: str,
    url: str,
    project_tokens: List[str],
    city: Optional[str],
    rera_pat: Optional[re.Pattern],
) -> bool:
    hay = " ".join([snippet or "", url or "", text[:4000] if text else ""])
    if rera_pat and rera_pat.search(hay.upper()):
        return True

    hits = _count_token_hits(hay, project_tokens)
    if project_tokens:
        # Require at least 2 token hits for multi-token names
        need = 2 if len(project_tokens) >= 2 else 1
        if hits >= need:
            return True

    # Fallback: city + at least one token
    if city and project_tokens:
        if _normalize(city) in _normalize(hay) and hits >= 1:
            return True

    return False


def _is_event_relevant(
    *,
    snippet: str,
    project_tokens: List[str],
    city: Optional[str],
    rera_pat: Optional[re.Pattern],
) -> bool:
    if not snippet:
        return False
    s = snippet

    if rera_pat and rera_pat.search(s.upper()):
        return True

    hits = _count_token_hits(s, project_tokens)
    if project_tokens:
        need = 2 if len(project_tokens) >= 2 else 1
        if hits >= need:
            return True

    if city and project_tokens:
        if _normalize(city) in _normalize(s) and hits >= 1:
            return True

    return False


def _date_in_range(iso: str, *, min_year: int = 2000, future_years: int = 3) -> bool:
    try:
        d = datetime.strptime(iso, "%Y-%m-%d").date()
    except Exception:
        return False
    today = datetime.utcnow().date()
    if d.year < min_year:
        return False
    if d > (today + timedelta(days=365 * future_years)):
        return False
    return True


def _find_events_in_text(text: str) -> List[Tuple[str, str, float, List[str]]]:
    """Returns list of (iso_date, snippet, confidence, tags)."""
    norm_text = " ".join(text.split())
    events: List[Tuple[str, str, float, List[str]]] = []

    for pat in DATE_PATTERNS:
        for m in pat.finditer(norm_text):
            iso = _parse_date_from_match(m)
            if not iso:
                continue
            if not _date_in_range(iso):
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


def _load_project_hints(evidence_path: Path) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Best-effort project hints from evidence.json (wide format) or path."""
    try:
        data = json.loads(evidence_path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("project"), dict):
            pj = data["project"]
            return pj.get("project_name"), pj.get("city"), pj.get("rera_id")
    except Exception:
        pass

    # Fallback: parse from slug folder name (very best-effort)
    try:
        slug = evidence_path.parent.parent.name  # artifacts/<slug>/<run_id>/evidence.json
        parts = slug.split("-")
        # Heuristic: last 5 tokens can look like RERA id pieces (e.g., ggm-582-314-2022-57)
        if len(parts) >= 6 and parts[-3].isdigit() and len(parts[-2]) == 4 and parts[-2].isdigit() and parts[-1].isdigit():
            rera_guess = f"{parts[-5].upper()}/{parts[-4]}/{parts[-3]}/{parts[-2]}/{parts[-1]}"
        else:
            rera_guess = None
        # City guess: second last chunk before rera block
        city_guess = None
        name_guess = None
        if rera_guess:
            city_guess = parts[-6]
            name_guess = " ".join(parts[:-6])
        else:
            # assume last token is city, rest is project
            if len(parts) >= 2:
                city_guess = parts[-1]
                name_guess = " ".join(parts[:-1])
        if name_guess:
            name_guess = name_guess.replace("  ", " ").strip()
        if city_guess:
            city_guess = city_guess.strip()
        return name_guess, city_guess, rera_guess
    except Exception:
        return None, None, None


def _infer_rera_from_docs(docs: List[Dict[str, Any]]) -> Optional[str]:
    """If evidence.json is missing rera_id, infer the most frequent RERA id pattern across extracted texts/snippets."""
    counts: Dict[str, int] = {}
    for d in docs[:40]:
        snippet = str(d.get("snippet") or "")
        for rid in _extract_rera_ids(snippet):
            counts[rid] = counts.get(rid, 0) + 2

        tp = str(d.get("textPath") or "")
        if tp:
            try:
                txt = load_text(tp)[:8000]
                for rid in _extract_rera_ids(txt):
                    counts[rid] = counts.get(rid, 0) + 1
            except Exception:
                pass

    if not counts:
        return None
    return sorted(counts.items(), key=lambda x: (-x[1], x[0]))[0][0]


def load_evidence(evidence_path: Path) -> List[Dict[str, Any]]:
    """Compatibility loader.

    Returns a list of per-doc dicts with keys:
      id, url, finalUrl, domain, snippet, textPath, textChars, needsOcr

    Supports:
      - old format: list[dict]
      - wide format: {"counts":..., "docs": [...]} (Step 6E)
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
    max_events_per_doc: int = 30,
    project_name: Optional[str] = None,
    city: Optional[str] = None,
    rera_id: Optional[str] = None,
) -> Tuple[List[TimelineEvent], List[TimelineEvent]]:
    """Extract dated, snippet-backed events.

    New (fix for polluted timelines): project relevance gating.
    - If project_name/city/rera_id are provided, we filter documents and individual events.
    - If not provided, we try to infer from evidence.json and the evidence_path.

    This prevents:
    - random nic.in pages (whitelist too broad) from polluting timelines
    - HRERA/cause-list PDFs containing multiple projects from leaking other projects' events
    """

    ev = load_evidence(evidence_path)

    # Best-effort hints
    pj_name, pj_city, pj_rera = _load_project_hints(evidence_path)
    project_name = project_name or pj_name
    city = city or pj_city
    rera_id = rera_id or pj_rera

    if not rera_id:
        rera_id = _infer_rera_from_docs(ev)

    project_tokens = _tokenize_project(project_name)
    rera_pat = _rera_pattern(rera_id)

    raw: List[TimelineEvent] = []

    for e in ev:
        if not isinstance(e, dict):
            continue
        if (e.get("textChars") or 0) <= 0:
            continue

        text = load_text(str(e.get("textPath") or ""))
        if not text.strip():
            continue

        # Doc-level gate
        if (project_tokens or rera_pat):
            if not _is_doc_relevant(
                text=text,
                snippet=str(e.get("snippet") or ""),
                url=str(e.get("finalUrl") or e.get("url") or ""),
                project_tokens=project_tokens,
                city=city,
                rera_pat=rera_pat,
            ):
                continue

        found = _find_events_in_text(text)
        found = sorted(found, key=lambda x: (-x[2], x[0]))

        kept: List[Tuple[str, str, float, List[str]]] = []
        for iso, snippet, conf, tags in found:
            if conf < min_confidence:
                continue

            # Event-level gate (critical for multi-case PDFs)
            if (project_tokens or rera_pat):
                if not _is_event_relevant(snippet=snippet, project_tokens=project_tokens, city=city, rera_pat=rera_pat):
                    continue

            # Validate snippet exists in normalized text
            if snippet not in " ".join(text.split()):
                continue

            kept.append((iso, snippet, conf, tags))
            if len(kept) >= max_events_per_doc:
                break

        for iso, snippet, conf, tags in kept:
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


def store_events(evidence_path, raw, deduped):
    """Writes events_raw.json, events_deduped.json, timeline.json.

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
