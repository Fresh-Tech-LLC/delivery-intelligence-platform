"""File-backed persistence for QA Studio records."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from backend.app.config import get_settings
from backend.app.services.qa.models import (
    ExecutionRunResult,
    ExecutionSpecSet,
    GuidedExplorationRun,
    NaturalLanguageScriptSet,
    PlaywrightGenerationSet,
    QaWorkspace,
    RegressionCandidate,
    ScenarioSet,
)

logger = logging.getLogger(__name__)


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8", newline="")
    tmp.replace(path)


class QaStore:
    """Persist QA workspaces and generated QA artifacts under local_data."""

    def __init__(self) -> None:
        self._root = get_settings().qa_workspace_dir

    def _dir(self, name: str) -> Path:
        path = self._root / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _path(self, group: str, qa_workspace_id: str) -> Path:
        return self._dir(group) / f"{qa_workspace_id}.json"

    def save_workspace(self, workspace: QaWorkspace) -> None:
        _atomic_write(self._path("workspaces", workspace.qa_workspace_id), workspace.model_dump_json(indent=2))

    def get_workspace(self, qa_workspace_id: str) -> QaWorkspace | None:
        path = self._path("workspaces", qa_workspace_id)
        if not path.exists():
            return None
        return QaWorkspace(**json.loads(path.read_text(encoding="utf-8")))

    def list_workspaces(self) -> list[QaWorkspace]:
        items: list[QaWorkspace] = []
        for path in sorted(self._dir("workspaces").glob("*.json")):
            try:
                items.append(QaWorkspace(**json.loads(path.read_text(encoding="utf-8"))))
            except Exception as exc:
                logger.warning("QaStore: failed to load %s (%s)", path.name, exc)
        return sorted(items, key=lambda item: (item.updated_at, item.qa_workspace_id), reverse=True)

    def save_scenarios(self, scenario_set: ScenarioSet) -> None:
        _atomic_write(self._path("scenarios", scenario_set.qa_workspace_id), scenario_set.model_dump_json(indent=2))

    def get_scenarios(self, qa_workspace_id: str) -> ScenarioSet | None:
        return self._load_single("scenarios", qa_workspace_id, ScenarioSet)

    def save_nl_scripts(self, script_set: NaturalLanguageScriptSet) -> None:
        _atomic_write(self._path("nl_scripts", script_set.qa_workspace_id), script_set.model_dump_json(indent=2))

    def get_nl_scripts(self, qa_workspace_id: str) -> NaturalLanguageScriptSet | None:
        return self._load_single("nl_scripts", qa_workspace_id, NaturalLanguageScriptSet)

    def save_execution_specs(self, spec_set: ExecutionSpecSet) -> None:
        _atomic_write(self._path("execution_specs", spec_set.qa_workspace_id), spec_set.model_dump_json(indent=2))

    def get_execution_specs(self, qa_workspace_id: str) -> ExecutionSpecSet | None:
        return self._load_single("execution_specs", qa_workspace_id, ExecutionSpecSet)

    def save_generated_tests(self, generation_set: PlaywrightGenerationSet) -> None:
        _atomic_write(self._path("generated_tests", generation_set.qa_workspace_id), generation_set.model_dump_json(indent=2))

    def get_generated_tests(self, qa_workspace_id: str) -> PlaywrightGenerationSet | None:
        return self._load_single("generated_tests", qa_workspace_id, PlaywrightGenerationSet)

    def save_exploration_runs(self, qa_workspace_id: str, runs: list[GuidedExplorationRun]) -> None:
        payload = [run.model_dump(mode="json") for run in runs]
        _atomic_write(self._path("exploration_runs", qa_workspace_id), json.dumps(payload, indent=2))

    def get_exploration_runs(self, qa_workspace_id: str) -> list[GuidedExplorationRun]:
        return self._load_list("exploration_runs", qa_workspace_id, GuidedExplorationRun)

    def save_run_results(self, qa_workspace_id: str, results: list[ExecutionRunResult]) -> None:
        payload = [result.model_dump(mode="json") for result in results]
        _atomic_write(self._path("run_results", qa_workspace_id), json.dumps(payload, indent=2))

    def get_run_results(self, qa_workspace_id: str) -> list[ExecutionRunResult]:
        return self._load_list("run_results", qa_workspace_id, ExecutionRunResult)

    def save_regression_candidates(self, qa_workspace_id: str, candidates: list[RegressionCandidate]) -> None:
        payload = [candidate.model_dump(mode="json") for candidate in candidates]
        _atomic_write(self._path("regression_candidates", qa_workspace_id), json.dumps(payload, indent=2))

    def get_regression_candidates(self, qa_workspace_id: str) -> list[RegressionCandidate]:
        return self._load_list("regression_candidates", qa_workspace_id, RegressionCandidate)

    def _load_single(self, group: str, qa_workspace_id: str, model_cls):
        path = self._path(group, qa_workspace_id)
        if not path.exists():
            return None
        return model_cls(**json.loads(path.read_text(encoding="utf-8")))

    def _load_list(self, group: str, qa_workspace_id: str, model_cls) -> list:
        path = self._path(group, qa_workspace_id)
        if not path.exists():
            return []
        raw = json.loads(path.read_text(encoding="utf-8"))
        return [model_cls(**item) for item in raw]


_STORE: QaStore | None = None


def get_qa_store() -> QaStore:
    global _STORE
    if _STORE is None:
        _STORE = QaStore()
    return _STORE
