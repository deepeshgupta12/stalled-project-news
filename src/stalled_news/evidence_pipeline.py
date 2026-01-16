from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .fetcher import fetch_url, stable_id_for_url
from .extractors import extract_from_html, extract_from_pdf
from .whitelist import host_from_url
from .models import SerpRun


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def load_serp_run(path: Path) -> SerpRun:
    return SerpRun.model_validate_json(path.read_text(encoding="utf-8"))


def make_run_dir_from_serp_results(serp_results_path: Path) -> Path:
    # serp_results.json is stored in <artifacts>/<project>/<run_id>/serp_results.json
    return serp_results_path.parent


def store_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def store_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build_snippets(text: str, max_snippets: int = 5, snippet_len: int = 280) -> List[str]:
    # Simple deterministic snippetting (we'll do smarter evidence spans later)
    t = " ".join(text.split())
    if not t:
        return []
    out = []
    step = max(1, len(t) // max_snippets)
    for i in range(0, min(len(t), step * max_snippets), step):
        out.append(t[i : i + snippet_len])
        if len(out) >= max_snippets:
            break
    return out


def fetch_and_extract_from_serp(serp_results_path: Path) -> Path:
    run_dir = make_run_dir_from_serp_results(serp_results_path)
    sources_dir = run_dir / "sources"
    texts_dir = run_dir / "texts"
    evidence_path = run_dir / "evidence.json"

    serp_run = load_serp_run(serp_results_path)

    evidence: List[Dict[str, Any]] = []
    for item in serp_run.results:
        url = str(item.link)
        doc_id = stable_id_for_url(url)
        domain = host_from_url(url) or ""

        fetched_at = _now_iso()
        fr = fetch_url(url)

        ct = fr.content_type or ""
        is_pdf = ("pdf" in ct) or fr.final_url.lower().endswith(".pdf")

        raw_path = sources_dir / f"{doc_id}.pdf" if is_pdf else sources_dir / f"{doc_id}.html"
        store_bytes(raw_path, fr.body)

        extracted = extract_from_pdf(fr.body) if is_pdf else extract_from_html(fr.body, fr.final_url)

        text_path = texts_dir / f"{doc_id}.txt"
        store_text(text_path, extracted.text)

        evidence.append(
            {
                "id": doc_id,
                "url": url,
                "finalUrl": fr.final_url,
                "domain": domain,
                "fetchedAt": fetched_at,
                "statusCode": fr.status_code,
                "contentType": ct,
                "title": extracted.title,
                "publishedDate": extracted.published_date,
                "textPath": str(text_path),
                "rawPath": str(raw_path),
                "sourceQuery": item.source_query,
                "snippets": build_snippets(extracted.text),
            }
        )

    evidence_path.write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
    return evidence_path
