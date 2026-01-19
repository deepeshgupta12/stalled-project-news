from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List, Optional

from .fetcher import fetch_url
from .extractors import extract_text_from_response
from .models import ProjectInput, SerpFetchMeta, SerpResult, SerpRun


def _project_from_source_query(q: Optional[str]) -> ProjectInput:
    """
    Tries to infer project_name/city/rera_id from a wide query like:
      "\"Zara Roma\" \"Gurgaon\" \"GGM/582/314/2022/57\""
    Falls back to 'unknown' if not parsable.
    """
    q = q or ""
    parts = re.findall(r'"([^"]+)"', q)
    project_name = parts[0].strip() if len(parts) >= 1 else "unknown"
    city = parts[1].strip() if len(parts) >= 2 else "unknown"
    rera_id = parts[2].strip() if len(parts) >= 3 else None

    # Pydantic constraint: min_length=2
    if len(project_name) < 2:
        project_name = "NA"
    if len(city) < 2:
        city = "NA"

    return ProjectInput(project_name=project_name, city=city, rera_id=rera_id)


def load_serp_run(path: Path) -> SerpRun:
    """
    Supports BOTH formats:
      A) Old format: full SerpRun JSON object (dict)
      B) New wide format: JSON list of result objects
    """
    data = json.loads(path.read_text(encoding="utf-8"))

    # A) Already a SerpRun object
    if isinstance(data, dict):
        return SerpRun.model_validate(data)

    # B) Wide format: list of results
    if not isinstance(data, list):
        raise ValueError(f"Unsupported SERP JSON format in {path}: expected dict or list, got {type(data)}")

    first_q = None
    for it in data:
        if isinstance(it, dict) and it.get("source_query"):
            first_q = it.get("source_query")
            break

    project = _project_from_source_query(first_q)
    meta = SerpFetchMeta(engine="google", max_results=10, gl="in", hl="en")

    results: List[SerpResult] = []
    for it in data:
        if not isinstance(it, dict):
            continue

        link = it.get("link") or it.get("url")
        title = it.get("title") or it.get("name")

        # SerpResult requires title + link
        if not link or not title:
            continue

        results.append(
            SerpResult(
                title=str(title),
                link=str(link),
                snippet=it.get("snippet"),
                position=it.get("position"),
                source_query=str(it.get("source_query") or first_q or ""),
            )
        )

    return SerpRun(
        project=project,
        meta=meta,
        results_total=len(results),
        results_whitelisted=len(results),
        results=results,
    )


def fetch_and_extract_from_serp(serp_results_path: str):
    serp_results_path = Path(serp_results_path)
    run_dir = serp_results_path.parent
    sources_dir = run_dir / "sources"
    texts_dir = run_dir / "texts"
    sources_dir.mkdir(parents=True, exist_ok=True)
    texts_dir.mkdir(parents=True, exist_ok=True)

    serp_run = load_serp_run(serp_results_path)
    results = serp_run.results

    evidence = []
    for r in results:
        url = r.link
        try:
            resp = fetch_url(url)
            text, meta = extract_text_from_response(resp, url=url)
            doc_id = meta.get("id") or meta.get("hash") or "doc"

            raw_ext = meta.get("ext") or ("pdf" if "pdf" in (meta.get("contentType") or "") else "html")
            raw_path = sources_dir / f"{doc_id}.{raw_ext}"
            txt_path = texts_dir / f"{doc_id}.txt"

            raw_path.write_bytes(meta.get("rawBytes") or b"")
            txt_path.write_text(text or "", encoding="utf-8")

            meta_out = {
                "id": doc_id,
                "url": url,
                "finalUrl": meta.get("finalUrl") or url,
                "domain": meta.get("domain"),
                "fetchedAt": meta.get("fetchedAt"),
                "statusCode": meta.get("statusCode"),
                "contentType": meta.get("contentType"),
                "title": meta.get("title"),
                "publishedDate": meta.get("publishedDate"),
                "textPath": str(txt_path),
                "rawPath": str(raw_path),
                "sourceQuery": r.source_query,
                "snippets": meta.get("snippets") or [],
                "needsOcr": meta.get("needsOcr"),
                "textChars": meta.get("textChars"),
            }
            evidence.append(meta_out)
        except Exception as e:
            evidence.append(
                {
                    "id": None,
                    "url": url,
                    "finalUrl": url,
                    "domain": None,
                    "fetchedAt": None,
                    "statusCode": None,
                    "contentType": None,
                    "title": None,
                    "publishedDate": None,
                    "textPath": None,
                    "rawPath": None,
                    "sourceQuery": r.source_query,
                    "snippets": [],
                    "error": str(e),
                }
            )

    out_path = run_dir / "evidence.json"
    out_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(out_path)
