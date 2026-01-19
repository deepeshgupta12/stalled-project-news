from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .fetcher import fetch_url, stable_id_for_url
from .extractors import extract_text_from_response
from .models import ProjectInput, SerpFetchMeta, SerpResult, SerpRun
from .whitelist import host_from_url


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_project_from_source_query(q: Optional[str]) -> ProjectInput:
    # Example: "\"Zara Roma\" \"Gurgaon\" \"GGM/582/314/2022/57\""
    if not q:
        return ProjectInput(project_name="unknown", city="unknown", rera_id=None)

    parts: List[str] = []
    buf = ""
    in_quote = False
    for ch in q:
        if ch == '"':
            if in_quote:
                if buf.strip():
                    parts.append(buf.strip())
                buf = ""
                in_quote = False
            else:
                in_quote = True
        elif in_quote:
            buf += ch

    project = parts[0] if len(parts) > 0 else "unknown"
    city = parts[1] if len(parts) > 1 else "unknown"
    rera = parts[2] if len(parts) > 2 else None
    return ProjectInput(project_name=project, city=city, rera_id=rera)


def load_serp_run(path: Path) -> SerpRun:
    raw = json.loads(path.read_text(encoding="utf-8"))

    # Case A: already object format
    if isinstance(raw, dict) and "results" in raw:
        return SerpRun.model_validate(raw)

    # Case B: wide list format
    if isinstance(raw, list):
        if len(raw) == 0:
            project = ProjectInput(project_name="unknown", city="unknown", rera_id=None)
            return SerpRun(project=project, meta=SerpFetchMeta(note="empty list"), results=[], results_total=0, results_whitelisted=0)

        # Two possible list variants:
        # (1) list of serp items directly
        # (2) list of blocks {source_query, results:[...]}
        items: List[Dict[str, Any]] = []
        if isinstance(raw[0], dict) and "results" in raw[0]:
            for block in raw:
                sq = block.get("source_query")
                for it in (block.get("results") or []):
                    it = dict(it)
                    it["source_query"] = it.get("source_query") or sq
                    items.append(it)
        else:
            items = [dict(x) for x in raw if isinstance(x, dict)]

        first_sq = items[0].get("source_query")
        project = _parse_project_from_source_query(first_sq)

        results: List[SerpResult] = []
        for it in items:
            link = it.get("link") or it.get("url")
            title = it.get("title") or ""
            domain = (it.get("domain") or host_from_url(link or "") or "").strip()
            snippet = it.get("snippet")
            date = it.get("date")
            source_query = it.get("source_query")

            if not link or not title or not domain:
                continue

            # Let pydantic validate URL; skip bad ones safely
            try:
                sr = SerpResult(
                    title=title,
                    link=link,
                    domain=domain,
                    snippet=snippet,
                    date=date,
                    source_query=source_query,
                )
                results.append(sr)
            except Exception:
                continue

        meta = SerpFetchMeta(note="loaded from wide list format")
        return SerpRun(
            project=project,
            meta=meta,
            results=results,
            results_total=len(results),
            results_whitelisted=len(results),
        )

    raise ValueError("Unsupported serp_results.json format")


def fetch_and_extract_from_serp(serp_results_path: Path) -> Path:
    serp_results_path = Path(serp_results_path)
    run_dir = serp_results_path.parent

    sources_dir = run_dir / "sources"
    texts_dir = run_dir / "texts"
    sources_dir.mkdir(parents=True, exist_ok=True)
    texts_dir.mkdir(parents=True, exist_ok=True)

    serp_run = load_serp_run(serp_results_path)

    evidence: List[Dict[str, Any]] = []

    successes = 0
    failures = 0
    total = 0

    for r in serp_run.results:
        total += 1
        url = str(r.link)
        doc_id = stable_id_for_url(url)

        # Skip if already extracted
        text_path = texts_dir / f"{doc_id}.txt"
        src_path = sources_dir / f"{doc_id}.bin"
        if text_path.exists() and src_path.exists():
            # still add evidence row
            txt = text_path.read_text(encoding="utf-8", errors="ignore")
            evidence.append(
                {
                    "domain": r.domain,
                    "url": url,
                    "finalUrl": url,
                    "title": r.title,
                    "snippet": r.snippet,
                    "sourceQuery": r.source_query,
                    "statusCode": 200,
                    "contentType": "cached",
                    "textChars": len(txt),
                    "needsOcr": False,
                    "sourcePath": str(src_path),
                    "textPath": str(text_path),
                    "fetchedAt": _utc_now(),
                }
            )
            continue

        try:
            resp = fetch_url(url)
        except Exception as e:
            failures += 1
            evidence.append(
                {
                    "domain": r.domain,
                    "url": url,
                    "finalUrl": url,
                    "title": r.title,
                    "snippet": r.snippet,
                    "sourceQuery": r.source_query,
                    "statusCode": 0,
                    "contentType": "error",
                    "textChars": 0,
                    "needsOcr": False,
                    "error": repr(e),
                    "sourcePath": "",
                    "textPath": "",
                    "fetchedAt": _utc_now(),
                }
            )
            continue

        # Save raw bytes
        src_path.write_bytes(resp.body)

        extracted = extract_text_from_response(resp.content_type, resp.body, resp.final_url)
        text_path.write_text(extracted.text or "", encoding="utf-8")

        if resp.status_code >= 200 and resp.status_code < 400:
            successes += 1
        else:
            failures += 1

        evidence.append(
            {
                "domain": host_from_url(resp.final_url) or r.domain,
                "url": resp.url,
                "finalUrl": resp.final_url,
                "title": r.title,
                "snippet": r.snippet,
                "sourceQuery": r.source_query,
                "statusCode": resp.status_code,
                "contentType": resp.content_type,
                "textChars": extracted.text_chars,
                "needsOcr": bool(extracted.needs_ocr),
                "sourcePath": str(src_path),
                "textPath": str(text_path),
                "fetchedAt": _utc_now(),
            }
        )

    evidence_path = run_dir / "evidence.json"
    evidence_path.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"evidence_written: {evidence_path}")
    print(f"successes={successes} failures={failures} total={total}")
    return evidence_path