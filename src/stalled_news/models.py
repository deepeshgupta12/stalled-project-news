from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ProjectInput(BaseModel):
    project_name: str
    city: str
    rera_id: Optional[str] = None


class SerpResult(BaseModel):
    title: Optional[str] = None
    link: str
    snippet: Optional[str] = None
    position: Optional[int] = None
    domain: Optional[str] = None
    source_query: Optional[str] = None

    # Optional extra fields (wide SERP includes these sometimes)
    section: Optional[str] = None
    source: Optional[str] = None
    date: Optional[str] = None


class SerpFetchMeta(BaseModel):
    engine: str = "google"
    gl: str = "in"
    hl: str = "en"
    requested_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")


# Backward-compat alias (some modules used SerpMeta earlier)
SerpMeta = SerpFetchMeta


class SerpRun(BaseModel):
    project: ProjectInput
    meta: SerpFetchMeta = Field(default_factory=SerpFetchMeta)
    results_total: int = 0
    results_whitelisted: int = 0
    results: List[SerpResult] = Field(default_factory=list)


class EvidenceDoc(BaseModel):
    doc_id: str
    url: str
    final_url: str
    domain: str
    snippet: str
    text_path: str


class ExtractedDoc(BaseModel):
    doc_id: str
    url: str
    final_url: str
    domain: str
    content_type: str
    text: str
    snippet: str
    raw_path: Optional[str] = None
    text_path: Optional[str] = None
    meta: Dict[str, Any] = Field(default_factory=dict)


# Backward-compat alias used by older code paths
SerpFetchMetaCompat = SerpFetchMeta
