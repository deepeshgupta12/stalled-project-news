from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from tenacity import RetryError

from .fetcher import fetch_url
from .models import EvidenceDoc, ExtractedDoc, ProjectInput, SerpMeta, SerpResult, SerpRun
from .extractors import extract_text_from_html, extract_text_from_pdf
from .whitelist import host_from_url


def _doc_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def load_serp_run(path: Path) -> SerpRun:
    raw = json.loads(path.read_text(encoding="utf-8"))

    # Case A: already a SerpRun-like object (dict)
    if isinstance(raw, dict):
        return SerpRun.model_validate(raw)

    # Case B: serp-run-wide format (list of {source_query, organic_results:[...]})
    if isinstance(raw, list):
        # Try to infer project from folder name: artifacts/<slug>/<run_id>/serp_results.json
        # slug like: zara-roma-gurgaon-ggm-582-314-2022-57
        slug = path.parent.parent.name if path.parent and path.parent.parent else "unknown"
        parts = slug.split("-")

        # heuristic: first token(s) = project, then city, rest maybe rera
        # We keep it simple: project_name = first 2 tokens if available, city = next 1 token.
        project_name = " ".join(parts[:2]).title() if len(parts) >= 2 else slug.title()
        city = parts[2].title() if len(parts) >= 3 else "Unknown"
        rera_id = None

        results: List[SerpResult] = []
        total = 0
        for block in raw:
            q = block.get("source_query")
            org = block.get("organic_results") or []
            for item in org:
                total += 1
                url = item.get("link") or item.get("url")
                if not url:
                    continue
                results.append(
                    SerpResult(
                        title=item.get("title") or "",
                        url=url,
                        final_url=None,
                        domain=host_from_url(url) or "",
                        snippet=item.get("snippet"),
                        date=item.get("date"),
                        source_query=q,
                    )
                )

        project = ProjectInput(project_name=project_name, city=city, rera_id=rera_id)
        return SerpRun(
            project=project,
            meta=SerpMeta(provider="serpapi", run_id=path.parent.name),
            results_total=total,
            results_whitelisted=len(results),
            results=results,
        )

    raise ValueError(f"Unsupported serp_results.json format: {type(raw)}")


def _write_text(text_dir: Path, doc_id: str, text: str) -> Path:
    text_dir.mkdir(parents=True, exist_ok=True)
    p = text_dir / f"{doc_id}.txt"
    p.write_text(text, encoding="utf-8", errors="ignore")
    return p


def _write_source(source_dir: Path, doc_id: str, content: bytes) -> Path:
    source_dir.mkdir(parents=True, exist_ok=True)
    p = source_dir / f"{doc_id}.bin"
    p.write_bytes(content or b"")
    return p


def _extract(url: str, final_url: str, status_code: int, content_type: str, content: bytes) -> ExtractedDoc:
    ct = (content_type or "").lower()
    if "pdf" in ct or final_url.lower().endswith(".pdf") or url.lower().endswith(".pdf"):
        return extract_text_from_pdf(url, final_url, content, status_code, content_type)
    return extract_text_from_html(url, final_url, content, status_code, content_type)


def fetch_and_extract_from_serp(serp_results_path: Path) -> Path:
    serp_results_path = serp_results_path.expanduser().resolve()
    run_dir = serp_results_path.parent
    texts_dir = run_dir / "texts"
    sources_dir = run_dir / "sources"

    serp_run = load_serp_run(serp_results_path)

    evidence: List[Dict[str, Any]] = []
    failures = 0
    successes = 0

    for r in serp_run.results:
        url = r.url
        doc_id = _doc_id(url)

        try:
            resp = fetch_url(url)
            final_url = str(resp.url)
            status_code = int(resp.status_code)
            content_type = resp.headers.get("content-type", "") or ""

            content = resp.content or b""
            extracted = _extract(url, final_url, status_code, content_type, content)

            text_path = _write_text(texts_dir, doc_id, extracted.text)
            source_path = _write_source(sources_dir, doc_id, content)

            ev = EvidenceDoc(
                doc_id=doc_id,
                url=url,
                final_url=final_url,
                domain=extracted.domain,
                content_type=extracted.content_type,
                status_code=extracted.status_code,
                title=extracted.title,
                published_date=extracted.published_date,
                text_path=str(text_path),
                source_path=str(source_path),
                snippet=r.snippet,
                textChars=extracted.text_chars,
                needsOcr=extracted.needs_ocr,
                error=extracted.error,
            ).model_dump()
            evidence.append(ev)
            successes += 1

        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.RemoteProtocolError) as e:
            failures += 1
            evidence.append(
                EvidenceDoc(
                    doc_id=doc_id,
                    url=url,
                    final_url=url,
                    domain=host_from_url(url) or "",
                    content_type="",
                    status_code=0,
                    snippet=r.snippet,
                    textChars=0,
                    needsOcr=False,
                    error=f"httpx_timeout: {type(e).__name__}: {e}",
                ).model_dump()
            )

        except RetryError as e:
            failures += 1
            evidence.append(
                EvidenceDoc(
                    doc_id=doc_id,
                    url=url,
                    final_url=url,
                    domain=host_from_url(url) or "",
                    content_type="",
                    status_code=0,
                    snippet=r.snippet,
                    textChars=0,
                    needsOcr=False,
                    error=f"retry_error: {e}",
                ).model_dump()
            )

        except Exception as e:
            failures += 1
            evidence.append(
                EvidenceDoc(
                    doc_id=doc_id,
                    url=url,
                    final_url=url,
                    domain=host_from_url(url) or "",
                    content_type="",
                    status_code=0,
                    snippet=r.snippet,
                    textChars=0,
                    needsOcr=False,
                    error=f"fetch_extract_error: {type(e).__name__}: {e}",
                ).model_dump()
            )

    out_path = run_dir / "evidence.json"
    out_path.write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"evidence_written: {out_path}")
    print(f"successes={successes} failures={failures} total={len(serp_run.results)}")

    return out_path
