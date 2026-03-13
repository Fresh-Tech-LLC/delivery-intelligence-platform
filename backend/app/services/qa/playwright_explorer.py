"""Bounded Playwright exploration scaffolding for QA Studio."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
import subprocess

from backend.app.config import get_settings
from backend.app.services.qa.models import ExplorationStatus, GuidedExplorationRun

logger = logging.getLogger(__name__)


class PlaywrightExplorer:
    """Run optional shell-backed guided exploration with durable run records."""

    def __init__(self) -> None:
        self._settings = get_settings()

    def start(
        self,
        qa_workspace_id: str,
        title: str,
        target_url: str | None,
        starting_context: str | None,
        steps_requested: int | None,
        browser_role: str | None,
    ) -> GuidedExplorationRun:
        if not self._settings.qa_playwright_enabled:
            raise ValueError("Playwright support is disabled by configuration.")
        if not self._settings.qa_exploration_enabled:
            raise ValueError("Guided exploration is disabled by configuration.")
        if not self._settings.qa_playwright_command:
            raise ValueError("QA_PLAYWRIGHT_COMMAND is not configured for guided exploration.")

        now = datetime.now(timezone.utc)
        run = GuidedExplorationRun(
            exploration_run_id=f"explore-{qa_workspace_id}-{now.strftime('%Y%m%d%H%M%S')}",
            qa_workspace_id=qa_workspace_id,
            title=title,
            target_url=target_url,
            starting_context=starting_context,
            steps_requested=max(1, min(steps_requested or self._settings.qa_max_exploration_steps, self._settings.qa_max_exploration_steps)),
            status=ExplorationStatus.DRAFT,
            created_at=now,
            extra={"browser_role": browser_role or self._settings.qa_default_browser_role},
        )

        output_dir = self._settings.qa_playwright_evidence_dir / qa_workspace_id / run.exploration_run_id
        output_dir.mkdir(parents=True, exist_ok=True)
        request_path = output_dir / "exploration_request.json"
        request_path.write_text(
            json.dumps(
                {
                    "qa_workspace_id": qa_workspace_id,
                    "exploration_run_id": run.exploration_run_id,
                    "title": title,
                    "target_url": target_url,
                    "starting_context": starting_context,
                    "steps_requested": run.steps_requested,
                    "browser_role": run.extra["browser_role"],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        command = f'{self._settings.qa_playwright_command} "{request_path}" "{output_dir}"'
        try:
            completed = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=300)
            result_path = output_dir / "exploration_result.json"
            if result_path.exists():
                payload = json.loads(result_path.read_text(encoding="utf-8"))
                run.status = ExplorationStatus(payload.get("status", "completed"))
                run.summary = payload.get("summary")
                run.discovered_screens = list(payload.get("discovered_screens", []))
                run.discovered_selectors = list(payload.get("discovered_selectors", []))
                run.evidence_refs = list(payload.get("evidence_refs", []))
            elif completed.returncode == 0:
                run.status = ExplorationStatus.COMPLETED
                run.summary = "Exploration command completed without a structured result payload."
            else:
                run.status = ExplorationStatus.FAILED
                run.summary = (completed.stderr or completed.stdout or "Exploration command failed.")[:500]
            run.extra["stdout"] = (completed.stdout or "")[:500]
            run.extra["stderr"] = (completed.stderr or "")[:500]
        except Exception as exc:
            logger.warning("PlaywrightExplorer.start failed: %s", exc)
            run.status = ExplorationStatus.FAILED
            run.summary = str(exc)

        run.completed_at = datetime.now(timezone.utc)
        return run
