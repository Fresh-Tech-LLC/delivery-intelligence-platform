from __future__ import annotations

from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Session workspace
# ---------------------------------------------------------------------------


class SessionWorkspace(BaseModel):
    session_id: str
    raw_notes: str = ""
    requirements_draft: str = ""
    story_set: Optional[dict[str, Any]] = None
    readiness_report: Optional[dict[str, Any]] = None
    uploaded_docs: list[str] = Field(default_factory=list)  # list of filenames (readiness)
    context_docs: dict[str, str] = Field(default_factory=dict)  # filename → extracted text (requirements)
    jira_project_ctx: Optional[dict[str, Any]] = None  # normalized JiraProjectContext, Power Mode


# ---------------------------------------------------------------------------
# Requirements
# ---------------------------------------------------------------------------


class RequirementsGenerateRequest(BaseModel):
    session_id: str
    raw_notes: str


class RequirementsUpdateRequest(BaseModel):
    session_id: str
    edit_instruction: str


class RequirementsResponse(BaseModel):
    session_id: str
    requirements: str
    clarifying_questions: list[str]
    assumptions: list[str]


# ---------------------------------------------------------------------------
# Stories (strict JSON schema)
# ---------------------------------------------------------------------------


class Priority(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"


class Subtask(BaseModel):
    title: str
    description: str = ""


class Story(BaseModel):
    id: str = ""
    title: str
    description: str
    acceptance_criteria: list[str]
    labels: list[str] = Field(default_factory=list)
    priority: Optional[Priority] = None
    dependencies: list[str] = Field(default_factory=list)
    notes: str = ""
    subtasks: list[Subtask] = Field(default_factory=list)


class Epic(BaseModel):
    id: str = ""
    title: str
    description: str
    labels: list[str] = Field(default_factory=list)
    priority: Optional[Priority] = None


class StorySet(BaseModel):
    epic: Epic
    stories: list[Story]


class StoriesGenerateRequest(BaseModel):
    session_id: str


class StoriesUpdateRequest(BaseModel):
    session_id: str
    edit_instruction: str


class StoriesResponse(BaseModel):
    session_id: str
    story_set: StorySet


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------


class Severity(str, Enum):
    blocker = "blocker"
    major = "major"
    minor = "minor"


class ReadinessFinding(BaseModel):
    severity: Severity
    category: str
    description: str
    suggested_fix: str


class ReadinessReport(BaseModel):
    score: int = Field(ge=0, le=100)
    summary: str
    findings: list[ReadinessFinding]


class ReadinessCheckRequest(BaseModel):
    session_id: str


class ReadinessResponse(BaseModel):
    session_id: str
    report: ReadinessReport


# ---------------------------------------------------------------------------
# Jira
# ---------------------------------------------------------------------------


class JiraCreateRequest(BaseModel):
    session_id: str
    dry_run: bool = True


class JiraIssuePayload(BaseModel):
    issue_type: str
    summary: str
    description: str
    labels: list[str] = Field(default_factory=list)
    priority: Optional[str] = None
    parent_key: Optional[str] = None


class JiraCreateResponse(BaseModel):
    dry_run: bool
    payloads: list[JiraIssuePayload] = Field(default_factory=list)
    created_keys: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Document upload
# ---------------------------------------------------------------------------


class DocUploadResponse(BaseModel):
    session_id: str
    filename: str
    message: str


# ---------------------------------------------------------------------------
# Power Mode — Jira discovery state
# ---------------------------------------------------------------------------


class JiraFieldInfo(BaseModel):
    field_id: str
    name: str
    custom: bool


class JiraProjectContext(BaseModel):
    project_key: str
    project_name: str
    project_id: str
    issue_types: list[str]
    statuses: list[str]
    field_mappings: list[JiraFieldInfo]  # project-relevant only: createmeta + sample-observed
    required_create_fields: dict[str, dict[str, str]]  # issuetype -> {field_id: display_name}
    can_create_issues: bool
    can_browse: bool
    hint_labels: list[str]       # heuristic from recent issue sample, not authoritative
    hint_components: list[str]   # heuristic from recent issue sample, not authoritative
    semantic_field_aliases: dict[str, str] = Field(default_factory=dict)
    # e.g. {"developer": "customfield_10200", "sprint": "customfield_10020", "team": "customfield_10300"}
    hint_teams: list[dict[str, str]] = Field(default_factory=list)
    # e.g. [{"name": "Platform Team", "id": "abc-123"}, ...]
    discovered_at: str


# ---------------------------------------------------------------------------
# PM / Jira query
# ---------------------------------------------------------------------------


class PMQueryRequest(BaseModel):
    session_id: str
    query: str


class JiraIssueResult(BaseModel):
    key: str
    summary: str
    status: str
    issue_type: str
    assignee: Optional[str] = None
    priority: Optional[str] = None


class PMQueryResponse(BaseModel):
    session_id: str
    jql: str
    results: list[JiraIssueResult]
    total: int


# ---------------------------------------------------------------------------
# Power Mode — requests / responses / events
# ---------------------------------------------------------------------------


class PowerDiscoverRequest(BaseModel):
    session_id: str


class PowerDiscoverResponse(BaseModel):
    session_id: str
    project_key: str
    project_name: str
    issue_types: list[str]
    statuses: list[str]
    custom_field_count: int
    can_create_issues: bool


class PowerRunRequest(BaseModel):
    session_id: str
    goal: str


class PowerStepEvent(BaseModel):
    type: str  # plan | action | observe | replan | result | error | done
    content: str
    jql: Optional[str] = None
    result_count: Optional[int] = None


# ---------------------------------------------------------------------------
# Generic error
# ---------------------------------------------------------------------------


class ErrorResponse(BaseModel):
    detail: str
