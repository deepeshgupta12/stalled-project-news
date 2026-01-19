from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .extractors import extract_text_from_html, extract_text_from_pdf
from .fetcher import fetch_url
from .models import EvidenceDoc, ProjectInput, SerpMeta, SerpResult, SerpRun
from .whitelist import host_from_url


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _doc_id_for(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def load_serp_run(path: Path) -> SerpRun:
    """
    Supports BOTH formats:
    1) Normal serp-run output: SerpRun JSON object
    2) Wide serp-run output: list[dict] items with keys like {source_query, title, link, snippet, domain, date}
    """
    raw = json.loads(path.read_text(encoding="utf-8"))

    # Format (1): full SerpRun object
    if isinstance(raw, dict):
        return SerpRun.model_validate(raw)

    # Format (2): wide list format
    if isinstance(raw, list):
        results: list[SerpResult] = []
        queries: list[str] = []

        for item in raw:
            if not isinstance(item, dict):
                continue

            url = item.get("link") or item.get("url") or item.get("finalUrl")
            if not url:
                continue

            sq = item.get("source_query")
            if sq and isinstance(sq, str) and sq not in queries:
                queries.append(sq)

            domain = item.get("domain")
            if not domain:
                try:
                    domain = host_from_url(url)
                except Exception:
                    domain = None

            results.append(
                SerpResult(
                    title=item.get("title"),
                    link=url,
                    snippet=item.get("snippet"),
                    position=item.get("position"),
                    domain=domain,
                    date=item.get("date"),
                    source_query=sq,
                )
            )

        # We may not have project/meta in list format, so synthesize minimal valid fields
        project = ProjectInput(project_name="unknown", city="unknown", rera_id=None)
        meta = SerpMeta(
            engine="serpapi",
            run_id=path.parent.name,
            stored_at=_utc_now_iso(),
            queries=queries,
        )

        return SerpRun(
            project=project,
            meta=meta,
            results=results,
            results_total=len(results),
            results_whitelisted=len(results),
        )

    raise ValueError(f"Unsupported serp_results.json format: {type(raw)}")


def fetch_and_extract_from_serp(serp_results_path: Path) -> Path:
    serp_run = load_serp_run(serp_results_path)

    run_dir = serp_results_path.parent
    sources_dir = run_dir / "sources"
    texts_dir = run_dir / "texts"
    sources_dir.mkdir(parents=True, exist_ok=True)
    texts_dir.mkdir(parents=True, exist_ok=True)

    evidence: list[dict[str, Any]] = []

    for r in serp_run.results:
        url = r.link
        if not url:
            continue

        fetch = fetch_url(url)
        final_url = fetch.final_url or url
        domain = host_from_url(final_url) if final_url else (r.domain or None)

        doc_id = _doc_id_for(final_url)

        ct = (fetch.content_type or "").lower()
        is_pdf = "pdf" in ct

        raw_ext = ".pdf" if is_pdf else ".html"
        raw_path = sources_dir / f"{doc_id}{raw_ext}"
        text_path = texts_dir / f"{doc_id}.txt"

        raw_path.write_bytes(fetch.content or b"")

        extracted_text = ""
        needs_ocr = False

        try:
            if is_pdf:
                extracted_text = extract_text_from_pdf(fetch.content or b"")
                if len(extracted_text.strip()) == 0:
                    needs_ocr = True
            else:
                extracted_text = extract_text_from_html(fetch.content or b"", base_url=final_url)
        except Exception:
            # Keep pipeline moving; evidence will show empty text
            extracted_text = ""

        text_path.write_text(extracted_text, encoding="utf-8")

        doc = EvidenceDoc(
            id=doc_id,
            url=url,
            finalUrl=final_url,
            domain=domain,
            fetchedAt=_utc_now_iso(),
            statusCode=fetch.status_code,
            contentType=fetch.content_type,
            title=r.title,
            publishedDate=r.date,
            textPath=str(text_path.resolve()),
            rawPath=str(raw_path.resolve()),
            sourceQuery=r.source_query,
            snippets=[r.snippet] if r.snippet else [],
            needsOcr=needs_ocr,
            textChars=len(extracted_text),
        )

        evidence.append(doc.model_dump())

    out_path = run_dir / "evidence.json"
    out_path.write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
    return out_path


def main_fetch_extract(serp_results: str) -> None:
    p = Path(serp_results).expanduser().resolve()
    out = fetch_and_extract_from_serp(p)
    print(f"evidence_stored: {out}")
