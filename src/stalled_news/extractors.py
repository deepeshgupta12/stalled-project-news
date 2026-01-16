from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import fitz  # pymupdf
import trafilatura
from trafilatura.metadata import extract_metadata


@dataclass(frozen=True)
class ExtractedDoc:
    title: Optional[str]
    published_date: Optional[str]  # keep as ISO-ish string if available
    text: str


def extract_from_html(html_bytes: bytes, url: str) -> ExtractedDoc:
    html = html_bytes.decode("utf-8", errors="replace")
    downloaded = trafilatura.extract(
        html,
        url=url,
        include_comments=False,
        include_tables=True,
        include_links=False,
        favor_precision=True,
    )
    text = (downloaded or "").strip()

    md = extract_metadata(html, default_url=url)
    title = getattr(md, "title", None) if md else None
    date = getattr(md, "date", None) if md else None

    published = None
    if date:
        published = str(date)

    return ExtractedDoc(title=title, published_date=published, text=text)


def extract_from_pdf(pdf_bytes: bytes) -> ExtractedDoc:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    parts = []
    for page in doc:
        parts.append(page.get_text("text"))
    text = "\n".join(parts).strip()

    title = None
    published = None
    meta = doc.metadata or {}
    # sometimes PDFs carry title/date metadata, often not reliable
    title = meta.get("title") or None
    published = meta.get("creationDate") or None

    return ExtractedDoc(title=title, published_date=str(published) if published else None, text=text)
