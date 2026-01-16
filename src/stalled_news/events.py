from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class EvidenceRef(BaseModel):
    doc_id: str = Field(..., description="Stable doc id from evidence.json")
    url: str
    final_url: str
    domain: str
    snippet: str = Field(..., description="Verbatim excerpt that must exist in extracted text")
    text_path: str


class TimelineEvent(BaseModel):
    date: str = Field(..., description="ISO date YYYY-MM-DD")
    claim: str = Field(..., description="Short sentence derived from snippet; must be consistent with snippet")
    evidence: EvidenceRef
    confidence: float = Field(..., ge=0.0, le=1.0)
    tags: list[str] = Field(default_factory=list)
