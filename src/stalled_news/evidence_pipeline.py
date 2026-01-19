from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .fetcher import fetch_url
from .models import (
    EvidenceDoc,
    ProjectInput,
    SerpFetchMeta,
    SerpResult,
    SerpRun,
    utc_now_iso,
)
from .extractors import extract_text_from_html, extract_text_from_pdf


def _sha_id(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def _domain_from_url(url: str) -> str:
    try:
        from urllib.parse import urlparse

        return (urlparse(url).netloc or "").lower()
    except Exception:
        return ""


def _content_type(headers: Dict[str, Any]) -> str:
    ct = headers.get("content-type") or headers.get("Content-Type") or ""
    if isinstance(ct, list):
        ct = ct[0] if ct else ""
    return str(ct).split(";")[0].strip().lower()


def load_serp_run(path: Path) -> SerpRun:
    """
    Supports BOTH formats:
    A) serp-run output: a SerpRun JSON object
    B) serp-run-wide output: a JSON list of result dicts
    """
    raw = path.read_text(encoding="utf-8", errors="ignore").strip()
    data = json.loads(raw) if raw else None

    # Format A: object
    if isinstance(data, dict):
        return SerpRun.model_validate(data)

    # Format B: list
    if isinstance(data, list):
        results: List[SerpResult] = []
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                continue

            link = item.get("link") or item.get("url") or item.get("finalUrl")
            if not link:
                continue

            title = str(item.get("title") or "")
            snippet = str(item.get("snippet") or item.get("serp_snippet") or "")
            source_query = str(item.get("source_query") or "")

            # position might be missing in wide runs
            pos = item.get("position")
            try:
                position = int(pos) if pos is not None else (i + 1)
            except Exception:
                position = i + 1

            try:
                sr = SerpResult(
                    title=title,
                    link=link,
                    snippet=snippet,
                    position=position,
                    source_query=source_query,
                )
                results.append(sr)
            except Exception:
                # If URL is not a valid HttpUrl, skip safely
                continue

        project = ProjectInput(project_name="unknown", city="unknown", rera_id=None)
        meta = SerpFetchMeta(engine="serpapi", max_results=len(results), gl="in", hl="en")
        return SerpRun(
            project=project,
            meta=meta,
            results_total=len(results),
            results_whitelisted=len(results),
            results=results,
        )

    raise ValueError(f"Unsupported serp_results format in: {path}")


def _out_dirs(run_dir: Path) -> Tuple[Path, Path]:
    sources_dir = run_dir / "sources"
    texts_dir = run_dir / "texts"
    sources_dir.mkdir(parents=True, exist_ok=True)
    texts_dir.mkdir(parents=True, exist_ok=True)
    return sources_dir, texts_dir


def _write_bytes(path: Path, b: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b)


def fetch_and_extract_from_serp(serp_results_path: Path) -> Path:
    run_dir = serp_results_path.parent
    sources_dir, texts_dir = _out_dirs(run_dir)

    serp_run = load_serp_run(serp_results_path)

    evidence: List[Dict[str, Any]] = []

    for r in serp_run.results:
        url = str(r.link)
        doc_id = _sha_id(url)
        domain = _domain_from_url(url)

        fetched_at = utc_now_iso()

        resp = fetch_url(url)
        status = getattr(resp, "status_code", None)

        # raw bytes + headers
        raw_bytes = getattr(resp, "content", b"")
        headers = dict(getattr(resp, "headers", {}) or {})
        final_url = str(getattr(resp, "url", url))
        ct = _content_type(headers)

        # Store raw
        raw_ext = "bin"
        if "pdf" in ct:
            raw_ext = "pdf"
        elif "html" in ct:
            raw_ext = "html"
        raw_path = sources_dir / f"{doc_id}.{raw_ext}"
        _write_bytes(raw_path, raw_bytes)

        # Extract text
        if "pdf" in ct or raw_ext == "pdf":
            ex = extract_text_from_pdf(raw_bytes)
        else:
            ex = extract_text_from_html(raw_bytes)

        text_path = texts_dir / f"{doc_id}.txt"
        text_path.write_text(ex.text or "", encoding="utf-8")

        ev = EvidenceDoc(
            doc_id=doc_id,
            url=url,
            final_url=final_url,
            domain=domain,
            fetched_at=fetched_at,
            status_code=status,
            content_type=ct if ct else ex.content_type,
            title=ex.title or (r.title if r.title else None),
            published_date=ex.published_date,
            raw_path=str(raw_path),
            text_path=str(text_path),
            source_query=r.source_query if r.source_query else None,
            serp_snippet=r.snippet if r.snippet else None,
            text_chars=ex.text_chars,
            needs_ocr=ex.needs_ocr,
            extra={},
        )

        evidence.append(ev.model_dump())

    out_path = run_dir / "evidence.json"
    out_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path