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
    # BA Mode — project + source selection
    jira_project_key: str = ""   # freeform; stored as-is; resolve_checklist() falls back to default if no custom file
    ba_source: str = "new"       # "new" | "existing_story"
    jira_story_key: str = ""     # Jira key being analysed (existing_story mode only)
    pulled_jira_story: Optional[dict[str, Any]] = None  # serialized PulledJiraStory


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
# BA Mode — pulled Jira story (normalized, not raw payload)
# ---------------------------------------------------------------------------


class PulledJiraStory(BaseModel):
    key: str
    summary: str
    description: str                                           # ADF flattened to plain text
    acceptance_criteria: list[str] = Field(default_factory=list)  # empty if not parseable
    ac_raw: str = ""                                           # original AC field value preserved
    status: str = ""
    priority: str = ""
    assignee: str = ""
    labels: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)
    issue_type: str = ""
    last_pulled_at: str                                        # ISO datetime of last fetch


# ---------------------------------------------------------------------------
# Manage Jira Projects
# ---------------------------------------------------------------------------


class ManagedProject(BaseModel):
    jira_project_key: str        # validated: [A-Z][A-Z0-9_-]{0,49}
    jira_project_name: str = ""  # populated from Jira if available; empty if lookup fails
    has_custom_checklist: bool = False  # derived from file presence on load, not stored state
    created_at: str              # ISO datetime


class ChecklistVersionInfo(BaseModel):
    version: int                 # monotonic, 1-indexed
    saved_at: str                # ISO datetime of archival


# Project API request/response models

class AddProjectRequest(BaseModel):
    jira_project_key: str


class AddProjectResponse(BaseModel):
    project: ManagedProject
    already_existed: bool


class RemoveProjectResponse(BaseModel):
    jira_project_key: str
    message: str


class ChecklistContentResponse(BaseModel):
    project_key: str             # "default" or a project key
    content: str
    current_version: int


class ChecklistSaveRequest(BaseModel):
    content: str


class ChecklistSaveResponse(BaseModel):
    project_key: str
    archived_as_version: Optional[int] = None  # None on first save (nothing archived yet)
    new_version: int                            # 1 on first save; archived + 1 on subsequent


class ChecklistHistoryResponse(BaseModel):
    project_key: str
    versions: list[ChecklistVersionInfo]


class ChecklistVersionContentResponse(BaseModel):
    project_key: str
    version: int
    saved_at: str
    content: str


class DeleteChecklistResponse(BaseModel):
    project_key: str
    deleted_files: int
    message: str


# ---------------------------------------------------------------------------
# Generic error
# ---------------------------------------------------------------------------


class ErrorResponse(BaseModel):
    detail: str
