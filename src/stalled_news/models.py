from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, HttpUrl


class ProjectInput(BaseModel):
    project_name: str = Field(min_length=2)
    city: str = Field(min_length=2)
    rera_id: Optional[str] = None


class SerpFetchMeta(BaseModel):
    provider: str = "serpapi"
    fetched_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    note: Optional[str] = None


# Backward-compatible alias used elsewhere in codebase
SerpMeta = SerpFetchMeta


class SerpResult(BaseModel):
    title: str
    link: HttpUrl
    domain: str
    snippet: Optional[str] = None
    date: Optional[str] = None
    source_query: Optional[str] = None


class SerpRun(BaseModel):
    project: ProjectInput
    meta: SerpFetchMeta
    results: List[SerpResult]
    results_total: int
    results_whitelisted: int


# Compatibility alias (some modules import this name)
SerpFetchMeta = SerpFetchMeta


# OPTIONAL: evidence structs (not strictly required, but kept for compatibility)
class ExtractedDoc(BaseModel):
    url: str
    final_url: str
    status_code: int
    content_type: str
    text: str
    text_chars: int
    needs_ocr: bool = False


class EvidenceDoc(BaseModel):
    domain: str
    url: str
    finalUrl: str
    title: Optional[str] = None
    snippet: Optional[str] = None
    sourceQuery: Optional[str] = None
    statusCode: int
    contentType: str
    textChars: int
    needsOcr: bool
    sourcePath: str
    textPath: str
    fetchedAt: str