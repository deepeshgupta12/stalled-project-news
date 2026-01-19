from __future__ import annotations

from typing import Optional

from bs4 import BeautifulSoup

from .models import ExtractedDoc
from .fetcher import stable_id_for_url


def _domain_from_url(url: str) -> str:
    try:
        from urllib.parse import urlparse
        return (urlparse(url).netloc or "").lower()
    except Exception:
        return ""


def extract_text_from_html(url: str, html: str, *, snippet: str = "", final_url: Optional[str] = None) -> ExtractedDoc:
    soup = BeautifulSoup(html or "", "html.parser")

    # Remove obvious noise
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text("\n")
    # Normalize whitespace a bit
    lines = [ln.strip() for ln in text.splitlines()]
    text = "\n".join([ln for ln in lines if ln])

    doc_id = stable_id_for_url(final_url or url)
    return ExtractedDoc(
        doc_id=doc_id,
        url=url,
        final_url=final_url or url,
        domain=_domain_from_url(final_url or url),
        content_type="text/html",
        text=text,
        snippet=snippet or "",
    )


def extract_text_from_pdf_bytes(url: str, pdf_bytes: bytes, *, snippet: str = "", final_url: Optional[str] = None) -> ExtractedDoc:
    # Best-effort PDF text extraction
    text = ""
    try:
        import fitz  # pymupdf
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            parts = []
            for page in doc:
                parts.append(page.get_text("text"))
            text = "\n".join(parts).strip()
    except Exception:
        text = ""

    doc_id = stable_id_for_url(final_url or url)
    return ExtractedDoc(
        doc_id=doc_id,
        url=url,
        final_url=final_url or url,
        domain=_domain_from_url(final_url or url),
        content_type="application/pdf",
        text=text,
        snippet=snippet or "",
    )
