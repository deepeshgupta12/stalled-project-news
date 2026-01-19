from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple

import trafilatura

# Optional PDF parsing
try:
    import fitz  # pymupdf
except Exception:  # pragma: no cover
    fitz = None


@dataclass(frozen=True)
class ExtractedDoc:
    text: str
    needs_ocr: bool = False
    text_chars: int = 0


def extract_from_html(html: str, base_url: str) -> ExtractedDoc:
    txt = trafilatura.extract(
        html,
        url=base_url,
        include_comments=False,
        include_tables=True,
        no_fallback=False,
    ) or ""
    txt = txt.strip()
    return ExtractedDoc(text=txt, needs_ocr=False, text_chars=len(txt))


def _pdf_text_pymupdf(pdf_bytes: bytes) -> str:
    if fitz is None:
        return ""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    parts = []
    for page in doc:
        parts.append(page.get_text("text") or "")
    return "\n".join(parts).strip()


def extract_from_pdf(pdf_bytes: bytes) -> ExtractedDoc:
    txt = _pdf_text_pymupdf(pdf_bytes)
    txt = (txt or "").strip()
    needs_ocr = (len(txt) == 0)
    return ExtractedDoc(text=txt, needs_ocr=needs_ocr, text_chars=len(txt))


# ---- Backward-compatible names used by your evolving pipeline ----

def extract_text_from_html(html: str, base_url: str) -> ExtractedDoc:
    return extract_from_html(html, base_url)


def extract_text_from_pdf(pdf_bytes: bytes) -> ExtractedDoc:
    return extract_from_pdf(pdf_bytes)


def extract_text_from_response(content_type: str, body: bytes, final_url: str) -> ExtractedDoc:
    ct = (content_type or "").split(";")[0].strip().lower()

    if "pdf" in ct or final_url.lower().endswith(".pdf"):
        return extract_from_pdf(body)

    # default HTML
    try:
        html = body.decode("utf-8", errors="ignore")
    except Exception:
        html = ""
    return extract_from_html(html, final_url)