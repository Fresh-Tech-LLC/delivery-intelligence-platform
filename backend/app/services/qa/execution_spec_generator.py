"""Deterministic execution-spec generation for QA Studio."""
from __future__ import annotations

from datetime import datetime, timezone

from backend.app.config import get_settings
from backend.app.services.qa.models import (
    ExecutionAction,
    ExecutionSpec,
    ExecutionSpecSet,
    ExecutionSpecStatus,
    ScenarioSet,
)

_ACTION_KINDS = ("navigate", "click", "fill", "select", "wait_for", "assert_text", "assert_visible", "capture_screenshot", "note")


class ExecutionSpecGenerator:
    """Convert scenarios into deterministic execution specs."""

    def __init__(self) -> None:
        self._settings = get_settings()

    def generate(self, qa_workspace_id: str, scenario_set: ScenarioSet) -> ExecutionSpecSet:
        specs: list[ExecutionSpec] = []
        for index, scenario in enumerate(scenario_set.scenarios[: self._settings.qa_max_execution_specs_per_run], start=1):
            actions = self._build_actions(scenario.steps, scenario.expected_results)
            specs.append(
                ExecutionSpec(
                    execution_spec_id=f"EXE-{index:03d}",
                    qa_workspace_id=qa_workspace_id,
                    scenario_id=scenario.scenario_id,
                    title=scenario.title,
                    actor=scenario.actor,
                    preconditions=list(scenario.preconditions),
                    actions=actions,
                    assertions=list(scenario.expected_results),
                    linked_refs=list(scenario.linked_refs),
                    status=ExecutionSpecStatus.DRAFT,
                    extra={"derived_from_scenario_id": scenario.scenario_id},
                )
            )
        return ExecutionSpecSet(
            spec_set_id=f"specs-{qa_workspace_id}",
            qa_workspace_id=qa_workspace_id,
            generated_at=datetime.now(timezone.utc),
            generation_notes="Deterministic transformation from scenario steps and expected results.",
            specs=specs,
        )

    def _build_actions(self, steps: list[str], expected_results: list[str]) -> list[ExecutionAction]:
        actions: list[ExecutionAction] = []
        step_index = 1
        for step in steps:
            kind, target, value = self._classify_step(step)
            actions.append(
                ExecutionAction(
                    action_id=f"ACT-{step_index:03d}",
                    kind=kind,
                    target=target,
                    value=value,
                    metadata={"source_step": step},
                )
            )
            step_index += 1
        for result in expected_results:
            actions.append(
                ExecutionAction(
                    action_id=f"ACT-{step_index:03d}",
                    kind="assert_text",
                    assertion=result,
                    metadata={"source_expected_result": result},
                )
            )
            step_index += 1
        actions.append(
            ExecutionAction(
                action_id=f"ACT-{step_index:03d}",
                kind="capture_screenshot",
                metadata={"note": "Capture final state for evidence."},
            )
        )
        return actions

    def _classify_step(self, step: str) -> tuple[str, str | None, str | None]:
        text = step.strip()
        lower = text.lower()
        if lower.startswith("navigate") or "open " in lower or "go to" in lower:
            return "navigate", text, None
        if "click" in lower or "select" in lower:
            return "click", text, None
        if "enter" in lower or "type" in lower or "fill" in lower:
            return "fill", text, None
        if "wait" in lower:
            return "wait_for", text, None
        if "see" in lower or "verify" in lower or "confirm" in lower:
            return "assert_visible", text, None
        return "note", text, None
