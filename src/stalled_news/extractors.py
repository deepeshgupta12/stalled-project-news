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
    needs_ocr: bool = False


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

    published = str(date) if date else None
    return ExtractedDoc(title=title, published_date=published, text=text, needs_ocr=False)


def _parse_pdf_date(s: Optional[str]) -> Optional[str]:
    # Common PDF metadata format: D:YYYYMMDDHHmmSS+05'30'
    if not s:
        return None
    s = str(s)
    if s.startswith("D:") and len(s) >= 10:
        y = s[2:6]
        m = s[6:8]
        d = s[8:10]
        if y.isdigit() and m.isdigit() and d.isdigit():
            return f"{y}-{m}-{d}"
    return None


def extract_from_pdf(pdf_bytes: bytes) -> ExtractedDoc:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        parts = []
        for page in doc:
            t = (page.get_text("text") or "").strip()
            if not t:
                # fallback: sometimes "blocks" captures more than plain text
                blocks = page.get_text("blocks") or []
                if blocks:
                    # block tuple: (x0, y0, x1, y1, "text", block_no, block_type)
                    t = "\n".join([b[4] for b in blocks if len(b) > 4 and isinstance(b[4], str)]).strip()
            if t:
                parts.append(t)

        text = "\n\n".join(parts).strip()

        meta = doc.metadata or {}
        title = meta.get("title") or None
        published = _parse_pdf_date(meta.get("creationDate")) or _parse_pdf_date(meta.get("modDate"))

        # If still empty, likely scanned/image PDF
        needs_ocr = (len(text) == 0)

        return ExtractedDoc(title=title, published_date=published, text=text, needs_ocr=needs_ocr)
    finally:
        doc.close()
