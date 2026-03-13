"""Phase 5 QA Studio models."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class QaWorkspaceStatus(str, Enum):
    DRAFT = "draft"
    SCENARIOS_GENERATED = "scenarios_generated"
    SCRIPTS_GENERATED = "scripts_generated"
    EXECUTION_SPECS_GENERATED = "execution_specs_generated"
    CODE_GENERATED = "code_generated"
    EXECUTED = "executed"


class TraceabilityLinkType(str, Enum):
    REQUIREMENT = "requirement"
    ACCEPTANCE_CRITERIA = "acceptance_criteria"
    BACKLOG_ITEM = "backlog_item"
    ARTIFACT = "artifact"
    CHUNK = "chunk"
    APPIAN_OBJECT = "appian_object"
    SCREENSHOT = "screenshot"


class TraceabilityLink(BaseModel):
    link_id: str
    link_type: TraceabilityLinkType
    ref_id: str
    title: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class QaWorkspace(BaseModel):
    qa_workspace_id: str
    source_workspace_id: str
    title: str
    project_key: str | None = None
    status: QaWorkspaceStatus = QaWorkspaceStatus.DRAFT
    created_at: datetime
    updated_at: datetime
    notes: str | None = None
    traceability_links: list[TraceabilityLink] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


class ScenarioPriority(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ScenarioStatus(str, Enum):
    DRAFT = "draft"
    REVIEWED = "reviewed"
    APPROVED = "approved"
    REJECTED = "rejected"


class TestScenario(BaseModel):
    scenario_id: str
    qa_workspace_id: str
    title: str
    objective: str
    actor: str
    preconditions: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    expected_results: list[str] = Field(default_factory=list)
    linked_refs: list[str] = Field(default_factory=list)
    priority: ScenarioPriority = ScenarioPriority.MEDIUM
    status: ScenarioStatus = ScenarioStatus.DRAFT
    extra: dict[str, Any] = Field(default_factory=dict)


class ScenarioSet(BaseModel):
    scenario_set_id: str
    qa_workspace_id: str
    generated_at: datetime
    generation_notes: str | None = None
    scenarios: list[TestScenario] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


class NaturalLanguageScript(BaseModel):
    script_id: str
    qa_workspace_id: str
    scenario_id: str
    title: str
    narrative: str
    linked_refs: list[str] = Field(default_factory=list)
    reviewed: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)


class NaturalLanguageScriptSet(BaseModel):
    script_set_id: str
    qa_workspace_id: str
    generated_at: datetime
    generation_notes: str | None = None
    scripts: list[NaturalLanguageScript] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


class ExecutionSpecStatus(str, Enum):
    DRAFT = "draft"
    REVIEWED = "reviewed"
    APPROVED = "approved"
    GENERATED = "generated"


class ExecutionAction(BaseModel):
    action_id: str
    kind: str
    target: str | None = None
    value: str | None = None
    assertion: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionSpec(BaseModel):
    execution_spec_id: str
    qa_workspace_id: str
    scenario_id: str
    title: str
    actor: str
    preconditions: list[str] = Field(default_factory=list)
    actions: list[ExecutionAction] = Field(default_factory=list)
    assertions: list[str] = Field(default_factory=list)
    linked_refs: list[str] = Field(default_factory=list)
    status: ExecutionSpecStatus = ExecutionSpecStatus.DRAFT
    extra: dict[str, Any] = Field(default_factory=dict)


class ExecutionSpecSet(BaseModel):
    spec_set_id: str
    qa_workspace_id: str
    generated_at: datetime
    generation_notes: str | None = None
    specs: list[ExecutionSpec] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


class GeneratedPlaywrightTest(BaseModel):
    test_id: str
    qa_workspace_id: str
    scenario_id: str
    execution_spec_id: str
    title: str
    file_path: str
    language: str = "typescript"
    linked_refs: list[str] = Field(default_factory=list)
    generated_at: datetime
    extra: dict[str, Any] = Field(default_factory=dict)


class PlaywrightGenerationSet(BaseModel):
    generation_set_id: str
    qa_workspace_id: str
    generated_at: datetime
    tests: list[GeneratedPlaywrightTest] = Field(default_factory=list)
    generation_notes: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ExplorationStatus(str, Enum):
    DRAFT = "draft"
    COMPLETED = "completed"
    FAILED = "failed"


class GuidedExplorationRun(BaseModel):
    exploration_run_id: str
    qa_workspace_id: str
    title: str
    target_url: str | None = None
    starting_context: str | None = None
    steps_requested: int
    status: ExplorationStatus = ExplorationStatus.DRAFT
    summary: str | None = None
    discovered_screens: list[str] = Field(default_factory=list)
    discovered_selectors: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    created_at: datetime
    completed_at: datetime | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ExecutionEvidence(BaseModel):
    evidence_id: str
    qa_workspace_id: str
    related_run_id: str | None = None
    evidence_type: str
    file_path: str | None = None
    summary: str | None = None
    created_at: datetime
    extra: dict[str, Any] = Field(default_factory=dict)


class FailureCategory(str, Enum):
    GENERATED_TEST_LOGIC_ISSUE = "generated_test_logic_issue"
    SELECTOR_OR_SYNC_ISSUE = "selector_or_sync_issue"
    TEST_DATA_ISSUE = "test_data_issue"
    ENVIRONMENT_ISSUE = "environment_issue"
    APPIAN_DEFECT = "appian_defect"
    REQUIREMENT_AMBIGUITY = "requirement_ambiguity"
    EXPECTED_BEHAVIOR_CHANGE = "expected_behavior_change"
    UNKNOWN = "unknown"


class ExecutionResultStatus(str, Enum):
    DRAFT = "draft"
    COMPLETED = "completed"
    FAILED = "failed"


class ExecutionRunResult(BaseModel):
    run_result_id: str
    qa_workspace_id: str
    execution_spec_id: str | None = None
    generated_test_id: str | None = None
    status: ExecutionResultStatus
    passed: bool
    failure_category: FailureCategory | None = None
    failure_summary: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    started_at: datetime
    completed_at: datetime | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class RegressionState(str, Enum):
    DRAFT = "draft"
    QA_REVIEWED = "qa_reviewed"
    EXECUTABLE = "executable"
    CANDIDATE_REGRESSION = "candidate_regression"
    APPROVED_REGRESSION = "approved_regression"
    DEPRECATED = "deprecated"


class RegressionCandidate(BaseModel):
    candidate_id: str
    qa_workspace_id: str
    scenario_id: str
    generated_test_id: str
    state: RegressionState
    rationale: str | None = None
    created_at: datetime
    updated_at: datetime
    extra: dict[str, Any] = Field(default_factory=dict)
