"""Phase 5 orchestration service for QA Studio."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from functools import lru_cache

from backend.app.config import get_settings
from backend.app.services.llm_client import get_llm_client
from backend.app.services.prompt_loader import get_prompt_loader
from backend.app.services.qa.evidence_store import EvidenceStore
from backend.app.services.qa.execution_spec_generator import ExecutionSpecGenerator
from backend.app.services.qa.models import (
    ExecutionResultStatus,
    ExecutionRunResult,
    ExecutionSpecSet,
    GuidedExplorationRun,
    NaturalLanguageScriptSet,
    PlaywrightGenerationSet,
    QaWorkspace,
    QaWorkspaceStatus,
    RegressionCandidate,
    RegressionState,
    ScenarioSet,
)
from backend.app.services.qa.nl_script_generator import NaturalLanguageScriptGenerator
from backend.app.services.qa.playwright_explorer import PlaywrightExplorer
from backend.app.services.qa.playwright_generator import PlaywrightGenerator
from backend.app.services.qa.qa_store import QaStore, get_qa_store
from backend.app.services.qa.regression_workflow import promote_candidate
from backend.app.services.qa.run_classifier import classify_failure
from backend.app.services.qa.scenario_generator import ScenarioGenerator
from backend.app.services.qa.traceability import build_traceability_for_workspace
from backend.app.services.requirements.requirements_service import get_requirements_service


class QaService:
    """Thin orchestration layer for QA Studio workflows."""

    def __init__(self, store: QaStore | None = None) -> None:
        self._store = store if store is not None else get_qa_store()
        self._settings = get_settings()
        self._requirements = get_requirements_service()
        self._evidence = EvidenceStore()

    def create_qa_workspace(self, source_workspace_id: str) -> QaWorkspace:
        source_workspace = self._requirements.get_workspace(source_workspace_id)
        if source_workspace is None:
            raise ValueError(f"Source workspace '{source_workspace_id}' not found.")
        existing = self.get_qa_workspace(f"qa-{source_workspace_id}")
        now = datetime.now(timezone.utc)
        workspace = QaWorkspace(
            qa_workspace_id=f"qa-{source_workspace_id}",
            source_workspace_id=source_workspace_id,
            title=source_workspace.title,
            project_key=source_workspace.project_key,
            status=existing.status if existing is not None else QaWorkspaceStatus.DRAFT,
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
            notes=existing.notes if existing is not None else None,
            traceability_links=existing.traceability_links if existing is not None else [],
            extra={"source_request_summary": source_workspace.request_summary},
        )
        self._store.save_workspace(workspace)
        return workspace

    def list_qa_workspaces(self) -> list[QaWorkspace]:
        return self._store.list_workspaces()

    def get_qa_workspace(self, qa_workspace_id: str) -> QaWorkspace | None:
        return self._store.get_workspace(qa_workspace_id)

    def build_traceability(self, qa_workspace_id: str):
        workspace = self._require_workspace(qa_workspace_id)
        links = build_traceability_for_workspace(self._requirements, workspace.source_workspace_id)
        workspace.traceability_links = links
        workspace.updated_at = datetime.now(timezone.utc)
        self._store.save_workspace(workspace)
        return links

    def generate_scenarios(self, qa_workspace_id: str, force_rebuild_traceability: bool = False) -> ScenarioSet:
        workspace = self._require_workspace(qa_workspace_id)
        if force_rebuild_traceability or not workspace.traceability_links:
            self.build_traceability(qa_workspace_id)
            workspace = self._require_workspace(qa_workspace_id)
        scenario_set = ScenarioGenerator(get_llm_client(), get_prompt_loader()).generate(
            qa_workspace_id=qa_workspace_id,
            title=workspace.title,
            requirements_draft=self._requirements.get_requirements_draft(workspace.source_workspace_id),
            backlog_draft=self._requirements.get_backlog_draft(workspace.source_workspace_id),
            traceability_refs=[link.ref_id for link in workspace.traceability_links],
            context_pack=self._requirements.get_context_pack(workspace.source_workspace_id),
        )
        self._store.save_scenarios(scenario_set)
        workspace.status = QaWorkspaceStatus.SCENARIOS_GENERATED
        workspace.updated_at = datetime.now(timezone.utc)
        self._store.save_workspace(workspace)
        return scenario_set

    def generate_nl_scripts(self, qa_workspace_id: str) -> NaturalLanguageScriptSet:
        workspace = self._require_workspace(qa_workspace_id)
        scenarios = self.get_scenarios(qa_workspace_id)
        if scenarios is None:
            raise ValueError("Generate scenarios before generating natural-language scripts.")
        script_set = NaturalLanguageScriptGenerator(get_llm_client(), get_prompt_loader()).generate(
            qa_workspace_id,
            scenarios.model_dump_json(indent=2),
        )
        self._store.save_nl_scripts(script_set)
        workspace.status = QaWorkspaceStatus.SCRIPTS_GENERATED
        workspace.updated_at = datetime.now(timezone.utc)
        self._store.save_workspace(workspace)
        return script_set

    def generate_execution_specs(self, qa_workspace_id: str) -> ExecutionSpecSet:
        workspace = self._require_workspace(qa_workspace_id)
        scenarios = self.get_scenarios(qa_workspace_id)
        if scenarios is None:
            raise ValueError("Generate scenarios before generating execution specs.")
        spec_set = ExecutionSpecGenerator().generate(qa_workspace_id, scenarios)
        self._store.save_execution_specs(spec_set)
        workspace.status = QaWorkspaceStatus.EXECUTION_SPECS_GENERATED
        workspace.updated_at = datetime.now(timezone.utc)
        self._store.save_workspace(workspace)
        return spec_set

    def generate_playwright_tests(self, qa_workspace_id: str, overwrite_existing: bool = True) -> PlaywrightGenerationSet:
        workspace = self._require_workspace(qa_workspace_id)
        spec_set = self.get_execution_specs(qa_workspace_id)
        if spec_set is None:
            raise ValueError("Generate execution specs before generating Playwright tests.")
        generation_set = PlaywrightGenerator().generate(qa_workspace_id, spec_set, overwrite_existing=overwrite_existing)
        self._store.save_generated_tests(generation_set)
        workspace.status = QaWorkspaceStatus.CODE_GENERATED
        workspace.updated_at = datetime.now(timezone.utc)
        self._store.save_workspace(workspace)
        return generation_set

    def start_guided_exploration(
        self,
        qa_workspace_id: str,
        title: str,
        target_url: str | None = None,
        starting_context: str | None = None,
        steps_requested: int | None = None,
        browser_role: str | None = None,
    ) -> GuidedExplorationRun:
        workspace = self._require_workspace(qa_workspace_id)
        run = PlaywrightExplorer().start(
            qa_workspace_id,
            title=title,
            target_url=target_url,
            starting_context=starting_context,
            steps_requested=steps_requested,
            browser_role=browser_role,
        )
        runs = self._store.get_exploration_runs(qa_workspace_id)
        runs.append(run)
        self._store.save_exploration_runs(qa_workspace_id, runs)
        workspace.updated_at = datetime.now(timezone.utc)
        self._store.save_workspace(workspace)
        return run

    def record_execution_result(
        self,
        qa_workspace_id: str,
        status: str,
        passed: bool,
        execution_spec_id: str | None = None,
        generated_test_id: str | None = None,
        failure_summary: str | None = None,
        evidence_summary: str | None = None,
        evidence_type: str = "text_summary",
    ) -> ExecutionRunResult:
        workspace = self._require_workspace(qa_workspace_id)
        now = datetime.now(timezone.utc)
        result = ExecutionRunResult(
            run_result_id=f"run-{qa_workspace_id}-{now.strftime('%Y%m%d%H%M%S%f')}",
            qa_workspace_id=qa_workspace_id,
            execution_spec_id=execution_spec_id,
            generated_test_id=generated_test_id,
            status=ExecutionResultStatus(status),
            passed=passed,
            failure_category=classify_failure(status, failure_summary),
            failure_summary=failure_summary,
            evidence_refs=[],
            started_at=now,
            completed_at=now,
            extra={},
        )
        if evidence_summary:
            evidence = self._evidence.save_text_summary(
                qa_workspace_id,
                related_run_id=result.run_result_id,
                summary=evidence_summary,
                evidence_type=evidence_type,
            )
            result.evidence_refs.append(evidence.evidence_id)

        results = self._store.get_run_results(qa_workspace_id)
        results.append(result)
        self._store.save_run_results(qa_workspace_id, results)
        workspace.status = QaWorkspaceStatus.EXECUTED
        workspace.updated_at = datetime.now(timezone.utc)
        self._store.save_workspace(workspace)

        if passed and generated_test_id:
            self._create_or_update_candidate(qa_workspace_id, generated_test_id, execution_spec_id)
        return result

    def promote_regression_candidate(
        self,
        qa_workspace_id: str,
        candidate_id: str,
        target_state: str,
        rationale: str | None = None,
    ) -> RegressionCandidate:
        candidates = self._store.get_regression_candidates(qa_workspace_id)
        candidate = next((item for item in candidates if item.candidate_id == candidate_id), None)
        if candidate is None:
            raise ValueError(f"Regression candidate '{candidate_id}' not found.")
        promote_candidate(candidate, RegressionState(target_state), rationale=rationale)
        self._store.save_regression_candidates(qa_workspace_id, candidates)
        return candidate

    def get_scenarios(self, qa_workspace_id: str):
        return self._store.get_scenarios(qa_workspace_id)

    def get_nl_scripts(self, qa_workspace_id: str):
        return self._store.get_nl_scripts(qa_workspace_id)

    def get_execution_specs(self, qa_workspace_id: str):
        return self._store.get_execution_specs(qa_workspace_id)

    def get_generated_tests(self, qa_workspace_id: str):
        return self._store.get_generated_tests(qa_workspace_id)

    def get_exploration_runs(self, qa_workspace_id: str):
        return self._store.get_exploration_runs(qa_workspace_id)

    def get_run_results(self, qa_workspace_id: str):
        return self._store.get_run_results(qa_workspace_id)

    def get_regression_candidates(self, qa_workspace_id: str):
        return self._store.get_regression_candidates(qa_workspace_id)

    def _create_or_update_candidate(self, qa_workspace_id: str, generated_test_id: str, execution_spec_id: str | None) -> RegressionCandidate:
        scenarios = self.get_scenarios(qa_workspace_id)
        scenario_id = ""
        if scenarios is not None and execution_spec_id:
            specs = self.get_execution_specs(qa_workspace_id)
            if specs is not None:
                match = next((item for item in specs.specs if item.execution_spec_id == execution_spec_id), None)
                if match is not None:
                    scenario_id = match.scenario_id
        candidate_id = self._candidate_id(qa_workspace_id, scenario_id, generated_test_id)
        candidates = self._store.get_regression_candidates(qa_workspace_id)
        existing = next((item for item in candidates if item.candidate_id == candidate_id), None)
        now = datetime.now(timezone.utc)
        target_state = (
            RegressionState(self._settings.qa_default_promotion_state)
            if self._settings.qa_default_promotion_state in {state.value for state in RegressionState}
            else RegressionState.DRAFT
        )
        if existing is None:
            existing = RegressionCandidate(
                candidate_id=candidate_id,
                qa_workspace_id=qa_workspace_id,
                scenario_id=scenario_id,
                generated_test_id=generated_test_id,
                state=target_state,
                created_at=now,
                updated_at=now,
                extra={"auto_created": True},
            )
            candidates.append(existing)
        else:
            existing.updated_at = now
        self._store.save_regression_candidates(qa_workspace_id, candidates)
        return existing

    def _candidate_id(self, qa_workspace_id: str, scenario_id: str, generated_test_id: str) -> str:
        digest = hashlib.sha1(f"{qa_workspace_id}|{scenario_id}|{generated_test_id}".encode("utf-8")).hexdigest()[:12]
        return f"candidate-{digest}"

    def _require_workspace(self, qa_workspace_id: str) -> QaWorkspace:
        workspace = self.get_qa_workspace(qa_workspace_id)
        if workspace is None:
            raise ValueError(f"QA workspace '{qa_workspace_id}' not found.")
        return workspace


@lru_cache
def get_qa_service() -> QaService:
    return QaService()
