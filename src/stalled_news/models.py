from __future__ import annotations

from typing import Any, Dict, List, Optional
from datetime import datetime

from pydantic import BaseModel, Field, HttpUrl


class ProjectInput(BaseModel):
    project_name: str = Field(min_length=2)
    city: str = Field(min_length=2)
    rera_id: Optional[str] = None


class SerpResult(BaseModel):
    title: str = Field(default="")
    link: HttpUrl
    snippet: str = Field(default="")
    position: int = Field(default=0)
    source_query: str = Field(default="")


class SerpFetchMeta(BaseModel):
    engine: str = Field(default="serpapi")
    max_results: int = Field(default=10)
    gl: str = Field(default="in")
    hl: str = Field(default="en")


# Backward-compatible alias (some pipeline steps use SerpMeta name)
class SerpMeta(SerpFetchMeta):
    pass


class SerpRun(BaseModel):
    """
    Canonical SERP run object (used by the original serp-run path).
    For serp-run-wide, we may reconstruct a SerpRun from a list format.
    """
    project: ProjectInput
    meta: SerpFetchMeta
    results_total: int
    results_whitelisted: int
    results: List[SerpResult] = Field(default_factory=list)


class ExtractedDoc(BaseModel):
    title: Optional[str] = None
    content_type: Optional[str] = None
    published_date: Optional[str] = None
    text: str = ""
    text_chars: int = 0
    needs_ocr: bool = False


class EvidenceDoc(BaseModel):
    doc_id: str
    url: str
    final_url: str
    domain: str

    fetched_at: str
    status_code: Optional[int] = None
    content_type: Optional[str] = None

    title: Optional[str] = None
    published_date: Optional[str] = None

    raw_path: Optional[str] = None
    text_path: Optional[str] = None

    # From SERP
    source_query: Optional[str] = None
    serp_snippet: Optional[str] = None

    # Extraction diagnostics
    text_chars: int = 0
    needs_ocr: bool = False

    extra: Dict[str, Any] = Field(default_factory=dict)


def utc_now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"