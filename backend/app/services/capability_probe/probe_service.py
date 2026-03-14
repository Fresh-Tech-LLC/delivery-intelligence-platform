"""
Orchestration layer for capability probe runs.

Responsibilities:
- Create and persist run records.
- Start background execution via a daemon thread.
- Expose run status for polling.
- Recover stuck runs on app startup.
"""
from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from backend.app.config import get_settings
from backend.app.models.capability_probe import (
    CapabilityProbeReport,
    CapabilityProbeRun,
    ProbeStatus,
)
from backend.app.services.capability_probe.probe_runner import PROBE_STEPS, ProbeRunner, grade_report
from backend.app.services.capability_probe.probe_store import ProbeStore, get_probe_store

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level concurrency guard
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_active_run_id: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ProbeService:
    def __init__(self, store: ProbeStore | None = None) -> None:
        self._store = store or get_probe_store()
        self._settings = get_settings()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_run(self) -> CapabilityProbeRun:
        """Create and persist a new probe run record in PENDING state."""
        run_id = uuid.uuid4().hex
        # probe_meta is populated at run-start by ProbeRunner (includes adapter_name).
        # Pre-populate with static config fields only.
        probe_meta: dict[str, Any] = {
            "llm_api_base": self._settings.llm_api_base,
            "probe_step_timeout": self._settings.probe_step_timeout,
        }
        run = CapabilityProbeRun(
            run_id=run_id,
            status=ProbeStatus.pending,
            started_at=_now(),
            total_steps=len(PROBE_STEPS),
            probe_meta=probe_meta,
        )
        self._store.save_run(run)
        return run

    def start_run(self, run_id: str) -> None:
        """
        Spawn a daemon thread to execute the probe.
        Raises ValueError if a run is already in progress.
        """
        global _active_run_id
        with _lock:
            if _active_run_id is not None:
                raise ValueError(
                    f"A probe run is already in progress: {_active_run_id}. "
                    "Wait for it to complete before starting a new one."
                )
            _active_run_id = run_id

        t = threading.Thread(target=self._execute, args=(run_id,), daemon=True)
        t.start()
        logger.info("Probe run %s started in background thread.", run_id)

    def get_run(self, run_id: str) -> CapabilityProbeRun | None:
        return self._store.get_run(run_id)

    def get_run_status(self, run_id: str) -> dict[str, Any]:
        """Return a compact status dict for the polling endpoint."""
        run = self._store.get_run(run_id)
        if run is None:
            return {"error": "run not found"}
        steps = self._store.get_steps(run_id) if run.steps == [] else run.steps
        return {
            "run_id": run.run_id,
            "status": run.status.value,
            "completed_steps": run.completed_steps,
            "total_steps": run.total_steps,
            "current_step": run.current_step,
            "finished_at": run.finished_at,
            "error": run.error,
            "steps": [
                {
                    "name": s.name,
                    "status": s.status.value,
                    "assessment": s.assessment.value,
                    "summary": s.summary,
                    "duration_ms": s.duration_ms,
                }
                for s in steps
            ],
        }

    def get_report(self, run_id: str) -> CapabilityProbeReport | None:
        return self._store.get_report(run_id)

    def list_runs(self) -> list[CapabilityProbeRun]:
        limit = self._settings.probe_max_runs_listed
        return self._store.list_runs(limit=limit)

    def active_run_id(self) -> str | None:
        with _lock:
            return _active_run_id

    def recover_stuck_runs(self) -> None:
        """
        Called at app startup. Marks any pending/running runs from a prior process as failed.
        Prevents ambiguous stale state after unexpected restarts.
        """
        runs = self._store.list_runs(limit=1000)
        recovered = 0
        for run in runs:
            if run.status in (ProbeStatus.pending, ProbeStatus.running):
                run.status = ProbeStatus.failed
                run.finished_at = _now()
                run.error = "Probe interrupted by app restart or process exit."
                run.current_step = None
                self._store.save_run(run)
                recovered += 1
        if recovered:
            logger.warning("Recovered %d stuck probe run(s) on startup.", recovered)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _execute(self, run_id: str) -> None:
        """Called in daemon thread. Clears _active_run_id when done."""
        global _active_run_id
        try:
            runner = ProbeRunner(
                store=self._store,
                settings=self._settings,
            )
            runner.run(run_id)
        except Exception as exc:
            logger.exception("ProbeService._execute failed for run %s: %s", run_id, exc)
            run = self._store.get_run(run_id)
            if run is not None and run.status not in (
                ProbeStatus.completed,
                ProbeStatus.failed,
            ):
                run.status = ProbeStatus.failed
                run.finished_at = _now()
                run.error = str(exc)[:500]
                run.current_step = None
                self._store.save_run(run)
        finally:
            with _lock:
                _active_run_id = None
            logger.info("Probe run %s finished.", run_id)


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_service_instance: ProbeService | None = None
_service_lock = threading.Lock()


def get_probe_service() -> ProbeService:
    global _service_instance
    with _service_lock:
        if _service_instance is None:
            _service_instance = ProbeService()
    return _service_instance
