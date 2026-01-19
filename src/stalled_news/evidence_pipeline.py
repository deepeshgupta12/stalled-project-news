from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .fetcher import fetch_url, stable_id_for_url
from .models import EvidenceDoc, ProjectInput, SerpFetchMeta, SerpResult, SerpRun
from .extractors import extract_text_from_html, extract_text_from_pdf_bytes


def _infer_project_from_path(p: Path) -> ProjectInput:
    """
    Best-effort inference from artifacts slug.
    If it can't infer safely, uses 'unknown'.
    """
    try:
        # artifacts/<slug>/<timestamp>/serp_results.json
        slug = p.parent.parent.name
        parts = slug.split("-")
        # Heuristic: project name until we hit a known city token is unreliable.
        # Keep it simple and safe.
        return ProjectInput(project_name=parts[0] if parts else "unknown", city="unknown", rera_id=None)
    except Exception:
        return ProjectInput(project_name="unknown", city="unknown", rera_id=None)


def load_serp_run(path: Path) -> SerpRun:
    """
    Supports:
    1) canonical SerpRun JSON object: {project, meta, results_total, results_whitelisted, results:[...]}
    2) wide SERP list JSON: [ {link,title,snippet,domain,source_query,...}, ... ]
    3) wide SERP wrapper object (if any): {whitelisted:[...]} or {results:[...]}
    """
    raw = path.read_text(encoding="utf-8", errors="ignore")
    data = json.loads(raw)

    # Case 1: canonical object
    if isinstance(data, dict) and "results" in data and "project" in data:
        return SerpRun.model_validate(data)

    # Case 2/3: list or wrapper
    items: List[Dict[str, Any]] = []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        if isinstance(data.get("whitelisted"), list):
            items = data["whitelisted"]
        elif isinstance(data.get("results"), list):
            items = data["results"]
        else:
            items = []

    project = _infer_project_from_path(path)
    meta = SerpFetchMeta()

    results: List[SerpResult] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        link = (it.get("link") or it.get("url") or "").strip()
        if not link:
            continue
        results.append(
            SerpResult(
                title=it.get("title"),
                link=link,
                snippet=it.get("snippet"),
                position=it.get("position"),
                domain=it.get("domain"),
                source_query=it.get("source_query"),
                section=it.get("section"),
                source=it.get("source"),
                date=it.get("date"),
            )
        )

    return SerpRun(
        project=project,
        meta=meta,
        results_total=len(results),
        results_whitelisted=len(results),
        results=results,
    )


def _write_bytes(path: Path, b: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b)


def _write_text(path: Path, s: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(s, encoding="utf-8", errors="ignore")


def fetch_and_extract_from_serp(serp_results_path: Path) -> Dict[str, Any]:
    """
    Fetch + extract every URL in serp_results into:
      - sources/<doc_id>.(html|pdf)
      - texts/<doc_id>.txt
    Writes evidence.json in the same run dir.
    Never crashes the pipeline on timeouts; counts failures and continues.
    """
    serp_run = load_serp_run(serp_results_path)

    run_dir = serp_results_path.parent
    sources_dir = run_dir / "sources"
    texts_dir = run_dir / "texts"
    sources_dir.mkdir(parents=True, exist_ok=True)
    texts_dir.mkdir(parents=True, exist_ok=True)

    docs: List[EvidenceDoc] = []
    seen: set[str] = set()

    successes = 0
    failures = 0
    total = 0

    for r in serp_run.results:
        url = (r.link or "").strip()
        if not url:
            continue

        doc_id = stable_id_for_url(url)
        if doc_id in seen:
            continue
        seen.add(doc_id)
        total += 1

        resp = fetch_url(url)
        if resp is None:
            failures += 1
            continue

        final_url = str(resp.url)
        ctype = (resp.headers.get("content-type") or "").lower().split(";")[0].strip()

        snippet = r.snippet or ""

        try:
            if "pdf" in ctype or final_url.lower().endswith(".pdf"):
                raw_path = sources_dir / f"{doc_id}.pdf"
                _write_bytes(raw_path, resp.content)
                extracted = extract_text_from_pdf_bytes(url, resp.content, snippet=snippet, final_url=final_url)
            else:
                raw_path = sources_dir / f"{doc_id}.html"
                html = resp.text
                _write_text(raw_path, html)
                extracted = extract_text_from_html(url, html, snippet=snippet, final_url=final_url)

            text_path = texts_dir / f"{doc_id}.txt"
            _write_text(text_path, extracted.text or "")

            docs.append(
                EvidenceDoc(
                    doc_id=doc_id,
                    url=url,
                    final_url=final_url,
                    domain=extracted.domain or (r.domain or ""),
                    snippet=snippet,
                    text_path=str(text_path),
                )
            )
            successes += 1
        except Exception:
            failures += 1
            continue

    evidence = {
        "project": serp_run.project.model_dump(),
        "meta": serp_run.meta.model_dump(),
        "serp_results_path": str(serp_results_path),
        "counts": {"total": total, "successes": successes, "failures": failures},
        "docs": [d.model_dump() for d in docs],
    }

    out_path = run_dir / "evidence.json"
    out_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {"evidence_path": str(out_path), "counts": evidence["counts"]}
