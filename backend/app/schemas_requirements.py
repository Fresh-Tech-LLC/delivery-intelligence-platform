from __future__ import annotations

from pydantic import BaseModel


class CreateWorkspaceRequest(BaseModel):
    title: str
    request_text: str
    project_key: str | None = None


class PinEvidenceRequest(BaseModel):
    ref_id: str
    rationale: str | None = None


class GenerateBacklogRequest(BaseModel):
    split_mode: str | None = None
