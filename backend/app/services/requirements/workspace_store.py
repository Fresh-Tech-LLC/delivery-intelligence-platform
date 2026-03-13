"""File-backed persistence for Requirements Studio workspaces and drafts."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from backend.app.config import get_settings
from backend.app.services.requirements.models import (
    BacklogDraft,
    ContextPack,
    FeatureWorkspace,
    RequirementsDraft,
)

logger = logging.getLogger(__name__)


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8", newline="")
    tmp.replace(path)


class WorkspaceStore:
    """Persist workspaces, context packs, and structured drafts to disk."""

    def __init__(self) -> None:
        self._root = get_settings().requirements_workspace_dir

    @property
    def _workspaces_dir(self) -> Path:
        path = self._root / "workspaces"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def _context_packs_dir(self) -> Path:
        path = self._root / "context_packs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def _requirements_dir(self) -> Path:
        path = self._root / "requirements"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def _backlogs_dir(self) -> Path:
        path = self._root / "backlogs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _workspace_path(self, workspace_id: str) -> Path:
        return self._workspaces_dir / f"{workspace_id}.json"

    def _context_pack_path(self, workspace_id: str) -> Path:
        return self._context_packs_dir / f"{workspace_id}.json"

    def _requirements_path(self, workspace_id: str) -> Path:
        return self._requirements_dir / f"{workspace_id}.json"

    def _backlog_path(self, workspace_id: str) -> Path:
        return self._backlogs_dir / f"{workspace_id}.json"

    def save_workspace(self, workspace: FeatureWorkspace) -> None:
        _atomic_write(self._workspace_path(workspace.workspace_id), workspace.model_dump_json(indent=2))

    def get_workspace(self, workspace_id: str) -> FeatureWorkspace | None:
        path = self._workspace_path(workspace_id)
        if not path.exists():
            return None
        return FeatureWorkspace(**json.loads(path.read_text(encoding="utf-8")))

    def list_workspaces(self) -> list[FeatureWorkspace]:
        results: list[FeatureWorkspace] = []
        for path in sorted(self._workspaces_dir.glob("*.json")):
            try:
                results.append(FeatureWorkspace(**json.loads(path.read_text(encoding="utf-8"))))
            except Exception as exc:
                logger.warning("WorkspaceStore: failed to load %s (%s)", path.name, exc)
        return sorted(results, key=lambda item: (item.updated_at, item.workspace_id), reverse=True)

    def delete_workspace(self, workspace_id: str) -> int:
        removed = 0
        for path in (
            self._workspace_path(workspace_id),
            self._context_pack_path(workspace_id),
            self._requirements_path(workspace_id),
            self._backlog_path(workspace_id),
        ):
            if not path.exists():
                continue
            path.unlink()
            removed += 1
        return removed

    def save_context_pack(self, context_pack: ContextPack) -> None:
        _atomic_write(
            self._context_pack_path(context_pack.workspace_id),
            context_pack.model_dump_json(indent=2),
        )

    def get_context_pack(self, workspace_id: str) -> ContextPack | None:
        path = self._context_pack_path(workspace_id)
        if not path.exists():
            return None
        return ContextPack(**json.loads(path.read_text(encoding="utf-8")))

    def save_requirements_draft(self, draft: RequirementsDraft) -> None:
        _atomic_write(self._requirements_path(draft.workspace_id), draft.model_dump_json(indent=2))

    def get_requirements_draft(self, workspace_id: str) -> RequirementsDraft | None:
        path = self._requirements_path(workspace_id)
        if not path.exists():
            return None
        return RequirementsDraft(**json.loads(path.read_text(encoding="utf-8")))

    def save_backlog_draft(self, draft: BacklogDraft) -> None:
        _atomic_write(self._backlog_path(draft.workspace_id), draft.model_dump_json(indent=2))

    def get_backlog_draft(self, workspace_id: str) -> BacklogDraft | None:
        path = self._backlog_path(workspace_id)
        if not path.exists():
            return None
        return BacklogDraft(**json.loads(path.read_text(encoding="utf-8")))


_STORE: WorkspaceStore | None = None


def get_workspace_store() -> WorkspaceStore:
    global _STORE
    if _STORE is None:
        _STORE = WorkspaceStore()
    return _STORE
