"""
Raw source data store — saves pre-normalisation payloads for auditability.

Capture failures are non-fatal: a warning is logged and ingestion continues.
Both methods return a ref string (path relative to knowledge_root()) that is
stored in ArtifactRecord.raw_ref for traceability.

Storage layout:
  local_data/knowledge/raw/jira/{run_id}/{issue_key}.json
  local_data/knowledge/raw/local/{run_id}/{dest_name}
"""
from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from backend.app.services.graph import paths

logger = logging.getLogger(__name__)


class RawStore:
    def save_jira_raw(self, run_id: str, issue_key: str, data: dict[str, Any]) -> str:
        """Write raw Jira JSON. Returns ref path relative to knowledge_root() with forward slashes."""
        dest = paths.raw_dir() / "jira" / run_id / f"{issue_key}.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            paths.atomic_write(dest, json.dumps(data, indent=2, default=str))
        except Exception as exc:
            logger.warning("RawStore: failed to write jira raw %s/%s (%s)", run_id, issue_key, exc)
        return dest.relative_to(paths.knowledge_root()).as_posix()

    def save_local_raw(self, run_id: str, source_path: Path, dest_name: str) -> str:
        """Copy source_path → raw/local/{run_id}/{dest_name}. Returns ref string."""
        dest = paths.raw_dir() / "local" / run_id / dest_name
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(source_path, dest)
        except Exception as exc:
            logger.warning("RawStore: failed to copy %s (%s)", source_path, exc)
        return dest.relative_to(paths.knowledge_root()).as_posix()


def get_raw_store() -> RawStore:
    return RawStore()
