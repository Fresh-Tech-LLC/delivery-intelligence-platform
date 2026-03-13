"""
File-based store for ArtifactRecord objects.

Storage: local_data/knowledge/normalized/artifacts/{artifact_id}.json
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from backend.app.services.graph import paths
from backend.app.services.graph.models import ArtifactRecord

logger = logging.getLogger(__name__)


class ArtifactStore:
    """Read/write ArtifactRecord objects to the local filesystem."""

    def _ensure_dirs(self) -> None:
        paths.ensure_knowledge_dirs()

    @property
    def _dir(self) -> Path:
        return paths.artifacts_dir()

    def save(self, record: ArtifactRecord) -> None:
        """Persist *record* to disk, overwriting any existing file for that artifact_id."""
        self._ensure_dirs()
        path = self._dir / f"{record.metadata.artifact_id}.json"
        paths.atomic_write(path, record.model_dump_json(indent=2))
        logger.debug("ArtifactStore: saved %s", record.metadata.artifact_id)

    def get(self, artifact_id: str) -> ArtifactRecord | None:
        """Return the ArtifactRecord for *artifact_id*, or None if not found."""
        path = self._dir / f"{artifact_id}.json"
        if not path.exists():
            return None
        try:
            return ArtifactRecord(**json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:
            logger.warning("ArtifactStore: failed to load %s (%s)", artifact_id, exc)
            return None

    def list_all(self) -> list[ArtifactRecord]:
        """Return all stored ArtifactRecords, sorted by artifact_id. Skips malformed files."""
        self._ensure_dirs()
        records: list[ArtifactRecord] = []
        for p in sorted(self._dir.glob("*.json")):
            try:
                records.append(ArtifactRecord(**json.loads(p.read_text(encoding="utf-8"))))
            except Exception as exc:
                logger.warning("ArtifactStore: skipping malformed file %s (%s)", p.name, exc)
        return records


def get_artifact_store() -> ArtifactStore:
    return ArtifactStore()
