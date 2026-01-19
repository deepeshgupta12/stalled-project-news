from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ProjectInput(BaseModel):
    project_name: str
    city: str
    rera_id: Optional[str] = None


class SerpMeta(BaseModel):
    provider: str = "serpapi"
    run_id: Optional[str] = None
    generated_at: Optional[str] = None
    notes: Optional[str] = None


class SerpResult(BaseModel):
    title: str = ""
    url: str
    final_url: Optional[str] = None
    domain: str = ""
    snippet: Optional[str] = None
    date: Optional[str] = None
    source_query: Optional[str] = None

    # allow extra keys from serp providers without breaking
    model_config = {"extra": "allow"}


class SerpRun(BaseModel):
    # Wide mode + normal mode both normalize into this
    project: ProjectInput
    meta: SerpMeta = Field(default_factory=SerpMeta)
    results_total: int = 0
    results_whitelisted: int = 0
    results: List[SerpResult] = Field(default_factory=list)

    model_config = {"extra": "allow"}


class ExtractedDoc(BaseModel):
    url: str
    final_url: str
    domain: str
    content_type: str
    status_code: int
    text: str
    title: Optional[str] = None
    published_date: Optional[str] = None
    needs_ocr: bool = False
    text_chars: int = 0
    error: Optional[str] = None

    model_config = {"extra": "allow"}


class EvidenceDoc(BaseModel):
    doc_id: str
    url: str
    final_url: str
    domain: str
    content_type: str
    status_code: int
    title: Optional[str] = None
    published_date: Optional[str] = None

    text_path: Optional[str] = None
    source_path: Optional[str] = None

    snippet: Optional[str] = None
    textChars: int = 0
    needsOcr: bool = False
    error: Optional[str] = None

    model_config = {"extra": "allow"}
