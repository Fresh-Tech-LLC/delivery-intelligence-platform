"""Deterministic Playwright code generation from execution specs."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from backend.app.config import get_settings
from backend.app.services.qa.models import ExecutionSpecSet, GeneratedPlaywrightTest, PlaywrightGenerationSet


class PlaywrightGenerator:
    """Generate inspectable TypeScript Playwright tests from execution specs."""

    def __init__(self) -> None:
        self._settings = get_settings()

    def generate(self, qa_workspace_id: str, spec_set: ExecutionSpecSet, overwrite_existing: bool = True) -> PlaywrightGenerationSet:
        if not self._settings.qa_playwright_enabled:
            raise ValueError("Playwright generation is disabled by configuration.")

        out_dir = self._settings.qa_playwright_tests_dir / qa_workspace_id
        out_dir.mkdir(parents=True, exist_ok=True)
        tests: list[GeneratedPlaywrightTest] = []
        for index, spec in enumerate(spec_set.specs, start=1):
            test_id = f"PW-{index:03d}"
            file_path = out_dir / f"{test_id.lower()}_{spec.execution_spec_id.lower()}.spec.ts"
            if overwrite_existing or not file_path.exists():
                file_path.write_text(self._render_test(spec), encoding="utf-8")
            tests.append(
                GeneratedPlaywrightTest(
                    test_id=test_id,
                    qa_workspace_id=qa_workspace_id,
                    scenario_id=spec.scenario_id,
                    execution_spec_id=spec.execution_spec_id,
                    title=spec.title,
                    file_path=str(file_path),
                    linked_refs=list(spec.linked_refs),
                    generated_at=datetime.now(timezone.utc),
                    extra={},
                )
            )
        return PlaywrightGenerationSet(
            generation_set_id=f"playwright-{qa_workspace_id}",
            qa_workspace_id=qa_workspace_id,
            generated_at=datetime.now(timezone.utc),
            tests=tests,
            generation_notes="Deterministic TypeScript generation from execution specs.",
        )

    def _render_test(self, spec) -> str:
        lines = [
            "import { test, expect } from '@playwright/test';",
            "",
            f"// scenario_id: {spec.scenario_id}",
            f"// execution_spec_id: {spec.execution_spec_id}",
            f"test('{spec.title}', async ({{ page }}) => {{",
        ]
        for precondition in spec.preconditions:
            lines.append(f"  // precondition: {precondition}")
        for action in spec.actions:
            lines.extend(self._render_action(action))
        for assertion in spec.assertions:
            lines.append(f"  // assertion: {assertion}")
        lines.append("});")
        lines.append("")
        return "\n".join(lines)

    def _render_action(self, action) -> list[str]:
        if action.kind == "navigate":
            return [f"  // navigate: {action.target or 'target not specified'}"]
        if action.kind == "click":
            return [f"  // click: {action.target or 'selector not specified'}"]
        if action.kind == "fill":
            return [f"  // fill: {action.target or 'field not specified'}"]
        if action.kind == "wait_for":
            return [f"  // wait_for: {action.target or 'condition not specified'}"]
        if action.kind == "assert_text":
            return [f"  // assert_text: {action.assertion or action.target or 'assertion not specified'}"]
        if action.kind == "assert_visible":
            return [f"  // assert_visible: {action.target or action.assertion or 'visibility target not specified'}"]
        if action.kind == "capture_screenshot":
            return [f"  // capture_screenshot"]
        return [f"  // note: {action.metadata.get('source_step') or action.target or action.assertion or ''}"]
