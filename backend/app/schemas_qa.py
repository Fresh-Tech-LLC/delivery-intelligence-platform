from __future__ import annotations

from pydantic import BaseModel


class CreateQaWorkspaceRequest(BaseModel):
    source_workspace_id: str


class GenerateScenariosRequest(BaseModel):
    force_rebuild_traceability: bool = False


class GenerateExecutionSpecsRequest(BaseModel):
    scenario_status: str | None = None


class GeneratePlaywrightTestsRequest(BaseModel):
    overwrite_existing: bool = True


class StartExplorationRequest(BaseModel):
    title: str
    target_url: str | None = None
    starting_context: str | None = None
    steps_requested: int | None = None
    browser_role: str | None = None


class RecordExecutionResultRequest(BaseModel):
    execution_spec_id: str | None = None
    generated_test_id: str | None = None
    status: str
    passed: bool
    failure_summary: str | None = None
    evidence_summary: str | None = None
    evidence_type: str = "text_summary"


class PromoteRegressionCandidateRequest(BaseModel):
    candidate_id: str
    target_state: str
    rationale: str | None = None
