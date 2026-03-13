"""
File-based store for IngestionRun objects.

Storage: local_data/knowledge/runs/{run_id}.json
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from backend.app.services.graph import paths
from backend.app.services.graph.models import IngestionRun

logger = logging.getLogger(__name__)


class RunStore:
    """Read/write IngestionRun objects to the local filesystem."""

    def _ensure_dirs(self) -> None:
        paths.ensure_knowledge_dirs()

    @property
    def _dir(self) -> Path:
        return paths.runs_dir()

    def save(self, run: IngestionRun) -> None:
        """Persist *run* to disk, overwriting any existing file for that run_id."""
        self._ensure_dirs()
        path = self._dir / f"{run.run_id}.json"
        paths.atomic_write(path, run.model_dump_json(indent=2))
        logger.debug("RunStore: saved %s", run.run_id)

    def get(self, run_id: str) -> IngestionRun | None:
        """Return the IngestionRun for *run_id*, or None if not found."""
        path = self._dir / f"{run_id}.json"
        if not path.exists():
            return None
        try:
            return IngestionRun(**json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:
            logger.warning("RunStore: failed to load %s (%s)", run_id, exc)
            return None

    def list_all(self) -> list[IngestionRun]:
        """Return all stored IngestionRuns, sorted by run_id. Skips malformed files."""
        self._ensure_dirs()
        runs: list[IngestionRun] = []
        for p in sorted(self._dir.glob("*.json")):
            try:
                runs.append(IngestionRun(**json.loads(p.read_text(encoding="utf-8"))))
            except Exception as exc:
                logger.warning("RunStore: skipping malformed file %s (%s)", p.name, exc)
        return runs


def get_run_store() -> RunStore:
    return RunStore()
