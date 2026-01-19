from __future__ import annotations

from typing import Optional

from .models import ExtractedDoc


def _safe_decode(b: bytes) -> str:
    try:
        return b.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def extract_text_from_html(raw: bytes) -> ExtractedDoc:
    """
    HTML -> plain text.
    Uses BeautifulSoup if available, else a very basic fallback.
    """
    html = _safe_decode(raw).strip()
    if not html:
        return ExtractedDoc(text="", text_chars=0, needs_ocr=False, content_type="text/html")

    text = ""
    title: Optional[str] = None

    # Prefer bs4 if installed
    try:
        from bs4 import BeautifulSoup  # type: ignore

        soup = BeautifulSoup(html, "html.parser")

        # Title
        t = soup.find("title")
        if t and t.get_text(strip=True):
            title = t.get_text(strip=True)[:300]

        # Remove script/style
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        text = soup.get_text(" ", strip=True)

    except Exception:
        # Fallback: strip tags crudely
        import re

        text = re.sub(r"<[^>]+>", " ", html)
        text = " ".join(text.split())

    return ExtractedDoc(
        title=title,
        content_type="text/html",
        text=text,
        text_chars=len(text),
        needs_ocr=False,
    )


def extract_text_from_pdf(raw: bytes) -> ExtractedDoc:
    """
    PDF -> text using PyMuPDF if available.
    If extracted text is empty, mark needs_ocr=True.
    """
    text = ""
    title: Optional[str] = None

    try:
        import fitz  # PyMuPDF  # type: ignore

        doc = fitz.open(stream=raw, filetype="pdf")
        parts = []
        for page in doc:
            parts.append(page.get_text("text"))
        text = "\n".join([p.strip() for p in parts if p and p.strip()]).strip()

        # Try PDF metadata title if present
        try:
            md = doc.metadata or {}
            if md.get("title"):
                title = str(md.get("title"))[:300]
        except Exception:
            pass

    except Exception:
        text = ""

    needs_ocr = len(text.strip()) == 0

    return ExtractedDoc(
        title=title,
        content_type="application/pdf",
        text=text,
        text_chars=len(text),
        needs_ocr=needs_ocr,
    )


# Backward-compatible helper (older code sometimes calls a single entry point)
def extract_text(content_type: str, raw: bytes) -> ExtractedDoc:
    ct = (content_type or "").lower()
    if "pdf" in ct:
        return extract_text_from_pdf(raw)
    return extract_text_from_html(raw)