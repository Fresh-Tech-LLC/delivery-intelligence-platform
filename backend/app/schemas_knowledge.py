"""Pydantic request models for /api/knowledge/* Phase 1 endpoints."""
from __future__ import annotations

from pydantic import BaseModel


class JiraIngestRequest(BaseModel):
    project_key: str | None = None
    jql: str | None = None
    max_results: int | None = None


class LocalDocsIngestRequest(BaseModel):
    root_dir: str | None = None
    project_key: str | None = None
    recursive: bool = True
