from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from bs4 import BeautifulSoup

try:
    import fitz  # PyMuPDF
except Exception:  # pragma: no cover
    fitz = None

from .models import ExtractedDoc
from .whitelist import host_from_url


def _clean_text(s: str) -> str:
    s = (s or "").replace("\u00a0", " ")
    s = " ".join(s.split())
    return s.strip()


def extract_text_from_html(url: str, final_url: str, html_bytes: bytes, status_code: int, content_type: str) -> ExtractedDoc:
    domain = host_from_url(final_url or url) or host_from_url(url) or ""
    try:
        soup = BeautifulSoup(html_bytes or b"", "html.parser")
        # Remove script/style
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        title = None
        if soup.title and soup.title.string:
            title = _clean_text(soup.title.string)

        text = _clean_text(soup.get_text(" "))
        return ExtractedDoc(
            url=url,
            final_url=final_url or url,
            domain=domain,
            content_type=content_type or "text/html",
            status_code=status_code,
            title=title,
            text=text,
            text_chars=len(text),
            needs_ocr=False,
        )
    except Exception as e:
        return ExtractedDoc(
            url=url,
            final_url=final_url or url,
            domain=domain,
            content_type=content_type or "text/html",
            status_code=status_code,
            title=None,
            text="",
            text_chars=0,
            needs_ocr=False,
            error=f"html_extract_error: {type(e).__name__}: {e}",
        )


def extract_text_from_pdf(url: str, final_url: str, pdf_bytes: bytes, status_code: int, content_type: str) -> ExtractedDoc:
    domain = host_from_url(final_url or url) or host_from_url(url) or ""
    if fitz is None:
        return ExtractedDoc(
            url=url,
            final_url=final_url or url,
            domain=domain,
            content_type=content_type or "application/pdf",
            status_code=status_code,
            title=None,
            text="",
            text_chars=0,
            needs_ocr=True,
            error="pymupdf_not_installed",
        )

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        parts = []
        for page in doc:
            parts.append(page.get_text("text") or "")
        text = _clean_text("\n".join(parts))
        needs_ocr = len(text) == 0
        return ExtractedDoc(
            url=url,
            final_url=final_url or url,
            domain=domain,
            content_type=content_type or "application/pdf",
            status_code=status_code,
            title=None,
            text=text,
            text_chars=len(text),
            needs_ocr=needs_ocr,
        )
    except Exception as e:
        return ExtractedDoc(
            url=url,
            final_url=final_url or url,
            domain=domain,
            content_type=content_type or "application/pdf",
            status_code=status_code,
            title=None,
            text="",
            text_chars=0,
            needs_ocr=True,
            error=f"pdf_extract_error: {type(e).__name__}: {e}",
        )


# Backward-compatible aliases (in case older code calls these)
def extract_from_html(url: str, final_url: str, html_bytes: bytes, status_code: int, content_type: str) -> ExtractedDoc:
    return extract_text_from_html(url, final_url, html_bytes, status_code, content_type)


def extract_from_pdf(url: str, final_url: str, pdf_bytes: bytes, status_code: int, content_type: str) -> ExtractedDoc:
    return extract_text_from_pdf(url, final_url, pdf_bytes, status_code, content_type)
