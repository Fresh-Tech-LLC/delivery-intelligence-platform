"""
File-based persistence for capability probe runs.

Storage layout:
    local_data/capability_probes/<run_id>/run.json
    local_data/capability_probes/<run_id>/steps.json
    local_data/capability_probes/<run_id>/report.json
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from backend.app.config import get_settings
from backend.app.models.capability_probe import (
    CapabilityProbeReport,
    CapabilityProbeRun,
    CapabilityProbeStepResult,
)

logger = logging.getLogger(__name__)


class ProbeStore:
    def __init__(self, base_dir: Path | None = None) -> None:
        settings = get_settings()
        self._base_dir: Path = base_dir or (settings.local_data_dir / "capability_probes")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_dir(self, run_id: str) -> Path:
        d = self._base_dir / run_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def _atomic_write(path: Path, data: Any) -> None:
        """Write JSON atomically via a temp file."""
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)  # replace() overwrites on all platforms including Windows

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def save_run(self, run: CapabilityProbeRun) -> None:
        path = self._run_dir(run.run_id) / "run.json"
        self._atomic_write(path, run.model_dump(mode="json"))

    def save_steps(self, run_id: str, steps: list[CapabilityProbeStepResult]) -> None:
        path = self._run_dir(run_id) / "steps.json"
        self._atomic_write(path, [s.model_dump(mode="json") for s in steps])

    def save_report(self, report: CapabilityProbeReport) -> None:
        path = self._run_dir(report.run_id) / "report.json"
        self._atomic_write(path, report.model_dump(mode="json"))

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def get_run(self, run_id: str) -> CapabilityProbeRun | None:
        path = self._base_dir / run_id / "run.json"
        if not path.exists():
            return None
        try:
            return CapabilityProbeRun.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Malformed run.json for run %s", run_id)
            return None

    def get_steps(self, run_id: str) -> list[CapabilityProbeStepResult]:
        path = self._base_dir / run_id / "steps.json"
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return [CapabilityProbeStepResult.model_validate(item) for item in data]
        except Exception:
            logger.warning("Malformed steps.json for run %s", run_id)
            return []

    def get_report(self, run_id: str) -> CapabilityProbeReport | None:
        path = self._base_dir / run_id / "report.json"
        if not path.exists():
            return None
        try:
            return CapabilityProbeReport.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Malformed report.json for run %s", run_id)
            return None

    # ------------------------------------------------------------------
    # List
    # ------------------------------------------------------------------

    def list_runs(self, limit: int = 20) -> list[CapabilityProbeRun]:
        """Return runs sorted by started_at descending, skipping malformed entries."""
        if not self._base_dir.exists():
            return []
        runs: list[CapabilityProbeRun] = []
        for path in self._base_dir.glob("*/run.json"):
            try:
                run = CapabilityProbeRun.model_validate_json(path.read_text(encoding="utf-8"))
                runs.append(run)
            except Exception:
                logger.warning("Skipping malformed run.json at %s", path)
        runs.sort(key=lambda r: r.started_at, reverse=True)
        return runs[:limit]


def get_probe_store() -> ProbeStore:
    return ProbeStore()
