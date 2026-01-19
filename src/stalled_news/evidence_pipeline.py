from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .extractors import extract_from_html, extract_from_pdf
from .fetcher import fetch_url
from .models import ExtractedDoc, EvidenceDoc, ProjectInput, SerpMeta, SerpResult, SerpRun


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_host(url: str) -> str:
    try:
        from .whitelist import host_from_url
        return host_from_url(url) or ""
    except Exception:
        return ""


def load_serp_run(path: Path) -> SerpRun:
    """
    Supports BOTH formats:
      A) canonical SerpRun JSON object (dict)
      B) serp-run-wide output list: [ {source_query,title,link,...}, ... ]
    """
    raw = path.read_text(encoding="utf-8")

    # Try canonical SerpRun first
    try:
        return SerpRun.model_validate_json(raw)
    except Exception:
        pass

    # Fallback: list format
    data = json.loads(raw)

    if isinstance(data, list):
        results: List[SerpResult] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            url = (item.get("link") or "").strip()
            if not url:
                continue
            dom = (item.get("domain") or "").strip() or _safe_host(url)
            sr = SerpResult(
                title=item.get("title"),
                link=url,
                snippet=item.get("snippet"),
                domain=dom or None,
                position=item.get("position"),
                source=item.get("source"),
                section=item.get("section"),
                date=item.get("date"),
                source_query=item.get("source_query"),
            )
            results.append(sr)

        # project/meta are unknown from list format; safe placeholders.
        project = ProjectInput(project_name="unknown", city="unknown", rera_id=None)
        meta = SerpMeta(provider="serpapi", engine="google", requested_at=_utc_now_iso(), query_count=None, note="loaded_from_list_format")

        return SerpRun(
            project=project,
            meta=meta,
            results=results,
            results_total=len(results),
            results_whitelisted=len(results),
        )

    # If it's not list, it must be broken
    raise ValueError(f"Unsupported serp_results.json format at {path}")


def build_evidence_docs(serp_run: SerpRun, out_dir: Path, limit: int = 20) -> List[EvidenceDoc]:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "sources").mkdir(parents=True, exist_ok=True)
    (out_dir / "texts").mkdir(parents=True, exist_ok=True)

    stored: List[EvidenceDoc] = []
    seen_urls = set()

    for r in serp_run.results:
        url = (r.link or "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)

        if len(stored) >= limit:
            break

        resp = fetch_url(url)
        if resp is None:
            continue

        content_type = (resp.headers.get("content-type") or "").lower()
        body = resp.content or b""

        extracted: Optional[ExtractedDoc] = None
        try:
            if "application/pdf" in content_type or url.lower().endswith(".pdf"):
                extracted = extract_from_pdf(body)
            else:
                # try decode html/text
                try:
                    html = body.decode("utf-8", errors="ignore")
                except Exception:
                    html = ""
                extracted = extract_from_html(html)
        except Exception:
            extracted = None

        if not extracted:
            continue

        doc_id = extracted.doc_id
        source_path = out_dir / "sources" / f"{doc_id}.bin"
        text_path = out_dir / "texts" / f"{doc_id}.txt"

        try:
            source_path.write_bytes(body)
        except Exception:
            pass

        try:
            text_path.write_text(extracted.text or "", encoding="utf-8")
        except Exception:
            pass

        ev = EvidenceDoc(
            doc_id=doc_id,
            url=url,
            final_url=str(resp.url) if getattr(resp, "url", None) else url,
            domain=r.domain or _safe_host(url) or "",
            title=r.title,
            source_query=r.source_query,
            fetched_at=_utc_now_iso(),
            content_type=content_type,
            text_path=str(text_path),
            source_path=str(source_path),
            text_chars=extracted.text_chars,
            needs_ocr=extracted.needs_ocr,
        )
        stored.append(ev)

    return stored


def fetch_and_extract_from_serp(serp_results_path: Path, out_dir: Optional[Path] = None, limit: int = 20) -> Path:
    serp_results_path = Path(serp_results_path)
    serp_run = load_serp_run(serp_results_path)

    if out_dir is None:
        # default next to serp_results.json (same run folder)
        out_dir = serp_results_path.parent

    out_dir = Path(out_dir)
    evidence = build_evidence_docs(serp_run, out_dir=out_dir, limit=limit)

    out_path = out_dir / "evidence.json"
    out_path.write_text(json.dumps([e.model_dump() for e in evidence], ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path
