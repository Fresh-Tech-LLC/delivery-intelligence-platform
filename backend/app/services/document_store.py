"""
File-based session workspace storage + managed-project registry/checklist storage.

Session layout:  local_data/<session_id>/
  - workspace.json          — SessionWorkspace (JSON)
  - docs/<filename>         — uploaded supporting documents

Managed project layout:  data/managed_projects/
  - registry.json           — list[ManagedProject]
  - checklists/default.md   — editable runtime default checklist (seeded from prompts/ on first access)
  - checklists/default.vN.md — archived versions
  - checklists/<KEY>.md     — custom checklist for project KEY
  - checklists/<KEY>.vN.md  — archived versions
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from backend.app.config import get_settings
from backend.app.schemas import (
    ChecklistContentResponse,
    ChecklistHistoryResponse,
    ChecklistSaveResponse,
    ChecklistVersionContentResponse,
    ChecklistVersionInfo,
    DeleteChecklistResponse,
    ManagedProject,
    SessionWorkspace,
)

logger = logging.getLogger(__name__)

# Safe Jira-like project key: starts with uppercase letter, then uppercase letters/digits/dash/underscore
_PROJECT_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_-]{0,49}$")


def validate_project_key(key: str) -> str:
    """Return key if valid, else raise ValueError."""
    if not _PROJECT_KEY_RE.match(key):
        raise ValueError(
            f"Invalid project key {key!r}. Must start with an uppercase letter "
            "and contain only uppercase letters, digits, hyphens, or underscores (max 50 chars)."
        )
    return key


def _atomic_write(path: Path, content: str) -> None:
    """Write to a temp file then atomically replace the target (near-atomic on Windows).

    Normalises line endings to LF before writing so that browser-submitted
    textarea content (which carries CRLF) does not accumulate extra blank lines
    on repeated saves.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content.replace("\r\n", "\n").replace("\r", "\n"), encoding="utf-8", newline="")
    tmp.replace(path)


class DocumentStore:
    def __init__(self) -> None:
        self._settings = get_settings()

    # ------------------------------------------------------------------
    # Session paths
    # ------------------------------------------------------------------

    def _session_dir(self, session_id: str) -> Path:
        d = self._settings.local_data_dir / session_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _docs_dir(self, session_id: str) -> Path:
        d = self._session_dir(session_id) / "docs"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _workspace_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "workspace.json"

    # ------------------------------------------------------------------
    # Managed-project paths
    # ------------------------------------------------------------------

    @property
    def _projects_dir(self) -> Path:
        return self._settings.data_dir / "managed_projects"

    @property
    def _registry_path(self) -> Path:
        return self._projects_dir / "registry.json"

    @property
    def _checklists_dir(self) -> Path:
        return self._projects_dir / "checklists"

    def _ensure_managed_projects_dirs(self) -> None:
        self._checklists_dir.mkdir(parents=True, exist_ok=True)

    def _current_checklist_path(self, key: str | None) -> Path:
        stem = "default" if key is None else key
        return self._checklists_dir / f"{stem}.md"

    def _version_path(self, key: str | None, version: int) -> Path:
        stem = "default" if key is None else key
        return self._checklists_dir / f"{stem}.v{version}.md"

    def _next_version_number(self, key: str | None) -> int:
        stem = "default" if key is None else key
        existing = list(self._checklists_dir.glob(f"{stem}.v*.md"))
        if not existing:
            return 1
        nums = []
        for p in existing:
            # stem looks like "default.v3" → split on ".v"
            parts = p.stem.rsplit(".v", 1)
            if len(parts) == 2 and parts[1].isdigit():
                nums.append(int(parts[1]))
        return max(nums) + 1 if nums else 1

    # ------------------------------------------------------------------
    # Single source of truth: does a project have a custom checklist file?
    # ------------------------------------------------------------------

    def _has_custom_checklist(self, key: str) -> bool:
        return self._current_checklist_path(key).exists()

    # ------------------------------------------------------------------
    # Workspace CRUD
    # ------------------------------------------------------------------

    def load_workspace(self, session_id: str) -> SessionWorkspace:
        path = self._workspace_path(session_id)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return SessionWorkspace(**data)
        return SessionWorkspace(session_id=session_id)

    def save_workspace(self, workspace: SessionWorkspace) -> None:
        path = self._workspace_path(workspace.session_id)
        _atomic_write(path, workspace.model_dump_json(indent=2))
        logger.debug("Saved workspace for session %s", workspace.session_id)

    # ------------------------------------------------------------------
    # Document upload (session docs)
    # ------------------------------------------------------------------

    def save_doc(self, session_id: str, filename: str, content: bytes) -> str:
        """Save an uploaded document. Returns the stored filename."""
        safe_name = Path(filename).name
        dest = self._docs_dir(session_id) / safe_name
        dest.write_bytes(content)
        logger.debug("Saved doc %s for session %s", safe_name, session_id)
        return safe_name

    def load_all_docs_text(self, session_id: str) -> str:
        """Return concatenated text content of all uploaded docs."""
        docs_dir = self._docs_dir(session_id)
        parts: list[str] = []
        for f in sorted(docs_dir.iterdir()):
            if f.suffix.lower() in (".txt", ".md"):
                parts.append(f"--- Document: {f.name} ---\n" + f.read_text(encoding="utf-8", errors="replace"))
            else:
                logger.debug("Skipping unsupported doc format: %s", f.name)
        return "\n\n".join(parts)

    def list_docs(self, session_id: str) -> list[str]:
        docs_dir = self._docs_dir(session_id)
        if not docs_dir.exists():
            return []
        return [f.name for f in sorted(docs_dir.iterdir())]

    # ------------------------------------------------------------------
    # Project registry
    # ------------------------------------------------------------------

    def load_project_registry(self) -> list[ManagedProject]:
        """Load registry.json. Returns [] if missing. Derives has_custom_checklist from file presence."""
        self._ensure_managed_projects_dirs()
        if not self._registry_path.exists():
            return []
        try:
            raw = json.loads(self._registry_path.read_text(encoding="utf-8"))
            projects = [ManagedProject(**p) for p in raw]
        except Exception as exc:
            logger.warning("DocumentStore: failed to load registry (%s); returning empty list", exc)
            return []
        # Derive has_custom_checklist from actual file presence (not stored state)
        for p in projects:
            p.has_custom_checklist = self._has_custom_checklist(p.jira_project_key)
        return projects

    def _save_project_registry(self, projects: list[ManagedProject]) -> None:
        self._ensure_managed_projects_dirs()
        data = [p.model_dump() for p in projects]
        _atomic_write(self._registry_path, json.dumps(data, indent=2))

    def add_project(self, key: str, name: str = "") -> tuple[ManagedProject, bool]:
        """
        Add project to registry if not already present.
        Returns (ManagedProject, already_existed).
        Never overwrites an existing non-empty name with an empty string.
        """
        validate_project_key(key)
        projects = self.load_project_registry()
        for p in projects:
            if p.jira_project_key == key:
                # Update name only if new name is non-empty and existing is empty
                if name and not p.jira_project_name:
                    p.jira_project_name = name
                    self._save_project_registry(projects)
                return p, True
        now = datetime.now(timezone.utc).isoformat()
        new_project = ManagedProject(
            jira_project_key=key,
            jira_project_name=name,
            has_custom_checklist=self._has_custom_checklist(key),
            created_at=now,
        )
        projects.append(new_project)
        self._save_project_registry(projects)
        logger.info("DocumentStore: registered project %s", key)
        return new_project, False

    def remove_project(self, key: str) -> None:
        """Remove project from registry only. Checklist files are NOT deleted."""
        projects = self.load_project_registry()
        filtered = [p for p in projects if p.jira_project_key != key]
        self._save_project_registry(filtered)
        logger.info("DocumentStore: removed project %s from registry (checklist files retained)", key)

    # ------------------------------------------------------------------
    # Checklist management
    # ------------------------------------------------------------------

    def load_checklist(self, key: str | None) -> str:
        """
        Load checklist content.
        key=None → default checklist (seeded from prompts/ on first access).
        key="PROJ" → custom checklist if file exists, else falls back to default.
        """
        self._ensure_managed_projects_dirs()
        if key is not None and not self._has_custom_checklist(key):
            # No custom file; fall back to default
            key = None
        current = self._current_checklist_path(key)
        if not current.exists() and key is None:
            # Seed default from prompts/
            seed_path = self._settings.prompts_dir / "ba_readiness_checklist.md"
            if seed_path.exists():
                content = seed_path.read_text(encoding="utf-8")
                _atomic_write(current, content)
                logger.info("DocumentStore: seeded default checklist from %s", seed_path)
            else:
                logger.warning("DocumentStore: seed file not found at %s", seed_path)
                return ""
        return current.read_text(encoding="utf-8") if current.exists() else ""

    def resolve_checklist(self, jira_project_key: str) -> str:
        """
        Canonical checklist resolution — single source of truth.
        1. If jira_project_key is non-empty and a custom checklist file exists → load it
        2. Otherwise → load default checklist
        """
        key = jira_project_key.strip() or None
        if key and not self._has_custom_checklist(key):
            key = None
        return self.load_checklist(key)

    def save_checklist(self, key: str | None, content: str) -> ChecklistSaveResponse:
        """
        Save checklist content with versioning.
        First save (no current file): write directly; archived_as_version=None, new_version=1.
        Subsequent saves: archive current as vN, write new content; archived_as_version=N, new_version=N+1.
        Validates: non-empty, < 500KB.
        """
        self._ensure_managed_projects_dirs()
        if not content.strip():
            raise ValueError("Checklist content must not be empty.")
        if len(content.encode("utf-8")) > 500 * 1024:
            raise ValueError("Checklist content exceeds 500KB limit.")

        project_key = "default" if key is None else key
        current = self._current_checklist_path(key)

        if not current.exists():
            # First save
            _atomic_write(current, content)
            logger.info("DocumentStore: first save for checklist %s (version 1)", project_key)
            return ChecklistSaveResponse(
                project_key=project_key,
                archived_as_version=None,
                new_version=1,
            )

        # Archive current, write new
        archive_version = self._next_version_number(key)
        archive_path = self._version_path(key, archive_version)
        _atomic_write(archive_path, current.read_text(encoding="utf-8"))
        _atomic_write(current, content)
        logger.info(
            "DocumentStore: saved checklist %s (archived v%d, new version %d)",
            project_key, archive_version, archive_version + 1,
        )
        return ChecklistSaveResponse(
            project_key=project_key,
            archived_as_version=archive_version,
            new_version=archive_version + 1,
        )

    def list_checklist_versions(self, key: str | None) -> ChecklistHistoryResponse:
        """Return version history sorted ascending by version number."""
        self._ensure_managed_projects_dirs()
        project_key = "default" if key is None else key
        stem = project_key
        existing = list(self._checklists_dir.glob(f"{stem}.v*.md"))
        versions: list[ChecklistVersionInfo] = []
        for p in existing:
            parts = p.stem.rsplit(".v", 1)
            if len(parts) == 2 and parts[1].isdigit():
                version_num = int(parts[1])
                saved_at = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat()
                versions.append(ChecklistVersionInfo(version=version_num, saved_at=saved_at))
        versions.sort(key=lambda v: v.version)
        return ChecklistHistoryResponse(project_key=project_key, versions=versions)

    def load_checklist_version(self, key: str | None, version: int) -> ChecklistVersionContentResponse:
        """Return content of a specific archived version."""
        self._ensure_managed_projects_dirs()
        project_key = "default" if key is None else key
        path = self._version_path(key, version)
        if not path.exists():
            raise FileNotFoundError(f"Version {version} not found for checklist {project_key!r}")
        content = path.read_text(encoding="utf-8")
        saved_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
        return ChecklistVersionContentResponse(
            project_key=project_key,
            version=version,
            saved_at=saved_at,
            content=content,
        )

    def restore_checklist_version(self, key: str | None, version: int) -> ChecklistSaveResponse:
        """
        Non-destructive restore:
        1. Load version N content
        2. Archive current as next version
        3. Write version N content as new current
        """
        self._ensure_managed_projects_dirs()
        version_data = self.load_checklist_version(key, version)
        return self.save_checklist(key, version_data.content)

    def get_checklist_content_response(self, key: str | None) -> ChecklistContentResponse:
        """Return current checklist content + version number."""
        self._ensure_managed_projects_dirs()
        project_key = "default" if key is None else key
        content = self.load_checklist(key)
        # Current version = next version - 1 (or 0 if no versions archived yet)
        history = self.list_checklist_versions(key)
        current_version = max((v.version for v in history.versions), default=0) + 1 if self._current_checklist_path(key).exists() else 0
        return ChecklistContentResponse(
            project_key=project_key,
            content=content,
            current_version=current_version,
        )

    def delete_checklist_files(self, key: str) -> DeleteChecklistResponse:
        """Permanently delete current + all history files for a project key. Never called for 'default'."""
        if key == "default":
            raise ValueError("The default checklist files cannot be deleted.")
        self._ensure_managed_projects_dirs()
        deleted = 0
        current = self._current_checklist_path(key)
        if current.exists():
            current.unlink()
            deleted += 1
        for vf in list(self._checklists_dir.glob(f"{key}.v*.md")):
            vf.unlink()
            deleted += 1
        logger.info("DocumentStore: deleted %d checklist file(s) for project %s", deleted, key)
        return DeleteChecklistResponse(
            project_key=key,
            deleted_files=deleted,
            message=f"Deleted {deleted} checklist file(s) for project {key}.",
        )


def get_document_store() -> DocumentStore:
    return DocumentStore()
