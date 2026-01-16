from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, HttpUrl


class ProjectInput(BaseModel):
    project_name: str = Field(..., min_length=2)
    city: str = Field(..., min_length=2)
    rera_id: Optional[str] = Field(default=None)


class SerpResult(BaseModel):
    title: str
    link: HttpUrl
    snippet: Optional[str] = None
    position: Optional[int] = None
    source_query: str


class SerpFetchMeta(BaseModel):
    engine: str
    max_results: int
    gl: str
    hl: str
    fetched_at: datetime = Field(default_factory=lambda: datetime.utcnow())


class SerpRun(BaseModel):
    project: ProjectInput
    meta: SerpFetchMeta
    results_total: int
    results_whitelisted: int
    results: list[SerpResult]
