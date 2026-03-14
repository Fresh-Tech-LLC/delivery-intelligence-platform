from __future__ import annotations

"""Operational state and history models for Requirements Studio workspaces."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class WorkspaceStage(str, Enum):
    CREATE_WORKSPACE = "create_workspace"
    BUILD_CONTEXT = "build_context"
    PIN_EVIDENCE = "pin_evidence"
    GENERATE_REQUIREMENTS = "generate_requirements"
    REVIEW_EDIT_REQUIREMENTS = "review_edit_requirements"
    GENERATE_BACKLOG = "generate_backlog"
    REVIEW_EXPORT_PUBLISH = "review_export_publish"


class StageStatusValue(str, Enum):
    COMPLETED = "completed"
    CURRENT = "current"
    UPCOMING = "upcoming"
    BLOCKED = "blocked"


class StageStatusRecord(BaseModel):
    stage: WorkspaceStage
    label: str
    status: StageStatusValue
    detail: str | None = None


class WorkspaceOperationalState(BaseModel):
    workspace_id: str
    current_stage: WorkspaceStage
    stage_statuses: list[StageStatusRecord] = Field(default_factory=list)
    pinned_evidence_ids: list[str] = Field(default_factory=list)
    latest_context_snapshot_id: str | None = None
    latest_requirements_draft_id: str | None = None
    latest_backlog_draft_id: str | None = None
    latest_validation_result_id: str | None = None
    latest_review_note_id: str | None = None
    generation_count: int = 0
    last_action: str | None = None
    last_updated_at: datetime
    warnings: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


class SnapshotSearchHit(BaseModel):
    ref_id: str
    artifact_id: str
    title: str
    source_system: str | None = None
    artifact_kind: str | None = None
    snippet: str = ""
    score: float = 0.0


class SnapshotRelatedHit(BaseModel):
    ref_id: str
    title: str
    source_system: str | None = None
    artifact_kind: str | None = None
    edge_types: list[str] = Field(default_factory=list)
    score: float = 0.0
    rationale: str | None = None


class ContextSnapshot(BaseModel):
    snapshot_id: str
    workspace_id: str
    created_at: datetime
    query_text: str
    summary_text: str
    selected_source_refs: list[str] = Field(default_factory=list)
    search_hits: list[SnapshotSearchHit] = Field(default_factory=list)
    related_hits: list[SnapshotRelatedHit] = Field(default_factory=list)
    pinned_items: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


class ReviewNoteType(str, Enum):
    ANALYST_NOTE = "analyst_note"
    AMBIGUITY = "ambiguity"
    GAP = "gap"
    REVIEW_COMMENT = "review_comment"


class ReviewNote(BaseModel):
    review_note_id: str
    workspace_id: str
    created_at: datetime
    note_type: ReviewNoteType
    title: str
    body: str
    linked_refs: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


class ValidationSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ValidationIssue(BaseModel):
    issue_id: str
    severity: ValidationSeverity
    category: str
    title: str
    description: str
    linked_refs: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


class ValidationTargetType(str, Enum):
    REQUIREMENTS_DRAFT = "requirements_draft"
    BACKLOG_DRAFT = "backlog_draft"
    WORKSPACE = "workspace"
    CONTEXT_SNAPSHOT = "context_snapshot"


class ValidationResult(BaseModel):
    validation_result_id: str
    workspace_id: str
    created_at: datetime
    target_type: ValidationTargetType
    target_id: str
    issues: list[ValidationIssue] = Field(default_factory=list)
    summary: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class GenerationType(str, Enum):
    CONTEXT_PACK = "context_pack"
    REQUIREMENTS = "requirements"
    BACKLOG = "backlog"
    VALIDATION = "validation"


class GenerationHistoryEntry(BaseModel):
    history_entry_id: str
    workspace_id: str
    created_at: datetime
    generation_type: GenerationType
    input_refs: list[str] = Field(default_factory=list)
    output_ref: str | None = None
    model_name: str | None = None
    notes: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
