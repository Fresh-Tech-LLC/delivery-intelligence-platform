from __future__ import annotations

from pydantic import BaseModel


class CreateWorkspaceRequest(BaseModel):
    title: str
    request_text: str
    project_key: str | None = None


class PinEvidenceRequest(BaseModel):
    ref_id: str
    title: str | None = None
    rationale: str | None = None


class UnpinEvidenceRequest(BaseModel):
    evidence_id: str


class ReviewUpdateRequest(BaseModel):
    assumptions_text: str | None = None
    open_questions_text: str | None = None
    problem_statement: str | None = None
    business_outcome: str | None = None
    requirements_generation_notes: str | None = None


class GenerateBacklogRequest(BaseModel):
    split_mode: str | None = None
