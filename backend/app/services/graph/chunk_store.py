"""
File-based store for ChunkRecord objects.

Storage: local_data/knowledge/chunks/{chunk_id}.json
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from backend.app.services.graph import paths
from backend.app.services.graph.models import ChunkRecord

logger = logging.getLogger(__name__)


class ChunkStore:
    """Read/write ChunkRecord objects to the local filesystem."""

    def _ensure_dirs(self) -> None:
        paths.ensure_knowledge_dirs()

    @property
    def _dir(self) -> Path:
        return paths.chunks_dir()

    def save(self, record: ChunkRecord) -> None:
        """Persist *record* to disk, overwriting any existing file for that chunk_id."""
        self._ensure_dirs()
        path = self._dir / f"{record.chunk_id}.json"
        paths.atomic_write(path, record.model_dump_json(indent=2))
        logger.debug("ChunkStore: saved %s", record.chunk_id)

    def get(self, chunk_id: str) -> ChunkRecord | None:
        """Return the ChunkRecord for *chunk_id*, or None if not found."""
        path = self._dir / f"{chunk_id}.json"
        if not path.exists():
            return None
        try:
            return ChunkRecord(**json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:
            logger.warning("ChunkStore: failed to load %s (%s)", chunk_id, exc)
            return None

    def list_all(self) -> list[ChunkRecord]:
        """Return all stored ChunkRecords, sorted by chunk_id. Skips malformed files."""
        self._ensure_dirs()
        records: list[ChunkRecord] = []
        for p in sorted(self._dir.glob("*.json")):
            try:
                records.append(ChunkRecord(**json.loads(p.read_text(encoding="utf-8"))))
            except Exception as exc:
                logger.warning("ChunkStore: skipping malformed file %s (%s)", p.name, exc)
        return records

    def list_by_artifact(self, artifact_id: str) -> list[ChunkRecord]:
        """Return all ChunkRecords belonging to *artifact_id*."""
        return [r for r in self.list_all() if r.artifact_id == artifact_id]


def get_chunk_store() -> ChunkStore:
    return ChunkStore()
