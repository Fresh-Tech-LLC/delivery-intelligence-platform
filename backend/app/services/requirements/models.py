"""Phase 4 Requirements Studio models."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class WorkspaceStatus(str, Enum):
    DRAFT = "draft"
    READY_FOR_REQUIREMENTS = "ready_for_requirements"
    REQUIREMENTS_GENERATED = "requirements_generated"
    BACKLOG_GENERATED = "backlog_generated"


class WorkspaceEvidenceType(str, Enum):
    ARTIFACT = "artifact"
    CHUNK = "chunk"
    RELATED_ARTIFACT = "related_artifact"
    MANUAL_NOTE = "manual_note"


class WorkspaceEvidenceItem(BaseModel):
    evidence_id: str
    evidence_type: WorkspaceEvidenceType
    ref_id: str
    title: str
    source_system: str | None = None
    artifact_kind: str | None = None
    rationale: str | None = None
    pinned: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class FeatureWorkspace(BaseModel):
    workspace_id: str
    title: str
    project_key: str | None = None
    request_text: str
    request_summary: str | None = None
    status: WorkspaceStatus = WorkspaceStatus.DRAFT
    created_at: datetime
    updated_at: datetime
    pinned_evidence: list[WorkspaceEvidenceItem] = Field(default_factory=list)
    generated_context_summary: str | None = None
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


class ContextSearchHit(BaseModel):
    ref_id: str
    artifact_id: str
    title: str
    source_system: str | None = None
    artifact_kind: str | None = None
    snippet: str
    score: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContextRelatedHit(BaseModel):
    ref_id: str
    title: str
    source_system: str | None = None
    artifact_kind: str | None = None
    edge_types: list[str] = Field(default_factory=list)
    score: float = 0.0
    rationale: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContextPack(BaseModel):
    workspace_id: str
    query_text: str
    search_hits: list[ContextSearchHit] = Field(default_factory=list)
    related_hits: list[ContextRelatedHit] = Field(default_factory=list)
    pinned_items: list[WorkspaceEvidenceItem] = Field(default_factory=list)
    selected_source_refs: list[str] = Field(default_factory=list)
    summary_text: str = ""
    warnings: list[str] = Field(default_factory=list)
    built_at: datetime


class RequirementPriority(str, Enum):
    MUST_HAVE = "must_have"
    SHOULD_HAVE = "should_have"
    COULD_HAVE = "could_have"
    UNKNOWN = "unknown"


class RequirementItem(BaseModel):
    requirement_id: str
    title: str
    description: str
    priority: RequirementPriority = RequirementPriority.UNKNOWN
    acceptance_criteria: list[str] = Field(default_factory=list)
    business_rules: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)


class RequirementsDraft(BaseModel):
    draft_id: str
    workspace_id: str
    title: str
    problem_statement: str
    business_outcome: str
    in_scope: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    requirements: list[RequirementItem] = Field(default_factory=list)
    generated_at: datetime
    generation_notes: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class BacklogItemType(str, Enum):
    EPIC = "epic"
    FEATURE = "feature"
    STORY = "story"
    TASK = "task"


class BacklogItemDraft(BaseModel):
    item_id: str
    item_type: BacklogItemType
    title: str
    description: str
    acceptance_criteria: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    team_hint: str | None = None
    source_refs: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


class BacklogDraft(BaseModel):
    backlog_id: str
    workspace_id: str
    title: str
    split_mode: str
    items: list[BacklogItemDraft] = Field(default_factory=list)
    generated_at: datetime
    generation_notes: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
