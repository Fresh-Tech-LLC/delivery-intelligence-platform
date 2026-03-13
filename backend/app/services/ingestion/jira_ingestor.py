"""
Jira ingestion source — fetches and normalises Jira issues into ArtifactRecord objects.

Reuses the existing JiraClient for all HTTP calls and authentication.
Deterministic artifact IDs (jira-{key.lower()}) ensure idempotent re-runs.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from backend.app.config import get_settings
from backend.app.services.graph.models import (
    ArtifactKind,
    ArtifactMetadata,
    ArtifactRecord,
    SourceSystem,
    SourceType,
)
from backend.app.services.ingestion.base import BaseIngestionSource
from backend.app.services.ingestion.raw_store import get_raw_store
from backend.app.services.jira_client import JiraClient, get_jira_client

logger = logging.getLogger(__name__)

_JIRA_DATE_FMT = "%Y-%m-%dT%H:%M:%S.%f%z"  # e.g. "2026-01-01T00:00:00.000+0000"

_KIND_MAP: dict[str, ArtifactKind] = {
    "epic": ArtifactKind.EPIC,
    "story": ArtifactKind.STORY,
    "task": ArtifactKind.TASK,
    "sub-task": ArtifactKind.TASK,
    "subtask": ArtifactKind.TASK,
    "bug": ArtifactKind.BUG,
    "defect": ArtifactKind.BUG,
    "requirement": ArtifactKind.REQUIREMENT,
    "specification": ArtifactKind.SPECIFICATION,
}

_FIELDS = (
    "summary,status,issuetype,assignee,priority,reporter,"
    "labels,description,comment,components,created,updated,parent,issuelinks"
)


def _parse_dt(value: str | None) -> datetime | None:
    """Parse a Jira timestamp string into a datetime. Returns None on failure."""
    if not value:
        return None
    try:
        return datetime.strptime(value, _JIRA_DATE_FMT)
    except ValueError:
        try:
            return datetime.fromisoformat(value)  # fallback for Jira Cloud v3 variants
        except Exception:
            logger.warning("_parse_dt: could not parse %r", value)
            return None


class JiraIngestionSource(BaseIngestionSource):
    """Fetches Jira issues and normalises them into ArtifactRecord objects."""

    def __init__(self, jira_client: JiraClient | None = None) -> None:
        self._client = jira_client if jira_client is not None else get_jira_client()

    @property
    def source_name(self) -> str:
        return "jira"

    @property
    def source_type(self) -> SourceType:
        return SourceType.TICKET

    def health_check(self) -> bool:
        if not self._client.is_configured():
            return False
        try:
            self._client.get_myself()
            return True
        except Exception as exc:
            logger.warning("JiraIngestionSource.health_check failed: %s", exc)
            return False

    def fetch_artifacts(self, run_id: str, **kwargs: Any) -> list[ArtifactRecord]:
        """Fetch Jira issues matching a JQL query and return normalised ArtifactRecords.

        JQL resolution priority:
          1. kwargs["jql"] if present
          2. Build 'project = "{key}" ORDER BY updated DESC' from first non-empty of:
             kwargs["project_key"] → settings.knowledge_default_project_key → settings.jira_project_key
          3. Raise ValueError if no project key or JQL available
        """
        settings = get_settings()

        jql: str
        project_key: str | None = None

        if kwargs.get("jql"):
            jql = kwargs["jql"]
        else:
            key = (
                kwargs.get("project_key")
                or settings.knowledge_default_project_key
                or settings.jira_project_key
                or ""
            )
            if not key:
                raise ValueError(
                    "no project_key, jql, or default project key configured — "
                    "pass project_key in the request body or set JIRA_PROJECT_KEY in .env"
                )
            project_key = key
            jql = f'project = "{key}" ORDER BY updated DESC'

        max_results = kwargs.get("max_results", settings.knowledge_jira_max_results)
        data = self._client.search_issues(jql=jql, max_results=max_results, fields=_FIELDS)
        issues = data.get("issues", [])
        raw_store = get_raw_store() if settings.knowledge_raw_capture_enabled else None
        base_url = settings.jira_base_url.rstrip("/")  # resolve once, passed to _normalize_issue

        artifacts: list[ArtifactRecord] = []
        for issue in issues:
            try:
                raw_ref = (
                    raw_store.save_jira_raw(run_id, issue["key"], issue)
                    if raw_store
                    else None
                )
                artifacts.append(
                    self._normalize_issue(issue, run_id, project_key, raw_ref, base_url)
                )
            except Exception as exc:
                logger.warning("skipping issue %s (%s)", issue.get("key", "?"), exc)
        return artifacts

    def _normalize_issue(
        self,
        issue: dict[str, Any],
        run_id: str,
        project_key: str | None,
        raw_ref: str | None,
        base_url: str,
    ) -> ArtifactRecord:
        f = issue.get("fields") or {}
        key = issue.get("key", "")
        issue_type = (f.get("issuetype") or {}).get("name", "")
        status_name = (f.get("status") or {}).get("name")
        priority_name = (f.get("priority") or {}).get("name")
        assignee = (f.get("assignee") or {}).get("displayName")
        reporter = (f.get("reporter") or {}).get("displayName")
        labels = f.get("labels") or []
        components = [c.get("name", "") for c in (f.get("components") or [])]
        parent_key = (f.get("parent") or {}).get("key")
        summary = f.get("summary") or ""
        description = f.get("description") or ""

        # text_content: readable markdown-style block
        parts = [f"# {summary}"]
        if description.strip():
            parts.append(description.strip())
        comments_raw = (f.get("comment") or {}).get("comments") or []
        comments = [
            ((c.get("author") or {}).get("displayName", "?"), (c.get("body") or "").strip())
            for c in comments_raw
            if (c.get("body") or "").strip()
        ]
        if comments:
            parts.append("## Comments")
            parts.extend(f"[{auth}]: {body}" for auth, body in comments)

        # artifact_id: stable, lowercase, idempotent across re-runs
        artifact_id = f"jira-{key.lower()}"

        # URL — base_url resolved once in fetch_artifacts and passed in
        url = f"{base_url}/browse/{key}" if base_url and key else None

        # project_key fallback: derive from issue key if not explicitly known
        resolved_pk = project_key or (key.rsplit("-", 1)[0] if "-" in key else None)

        kind = _KIND_MAP.get(issue_type.lower(), ArtifactKind.UNKNOWN)

        meta = ArtifactMetadata(
            artifact_id=artifact_id,
            source_type=SourceType.TICKET,
            source_system=SourceSystem.JIRA,
            external_id=issue.get("id", ""),
            project_key=resolved_pk,
            title=summary,
            artifact_kind=kind,
            author=reporter or assignee,
            created_at=_parse_dt(f.get("created")),
            updated_at=_parse_dt(f.get("updated")),
            tags=labels,
            status=status_name,
            url=url,
            ingestion_run_id=run_id,
        )
        extra: dict[str, Any] = {
            "issue_type": issue_type,
            "priority": priority_name,
            "assignee": assignee,
            "reporter": reporter,
            "components": components,
            "comment_count": len(comments),
        }
        if parent_key:
            extra["parent_key"] = parent_key

        return ArtifactRecord(
            metadata=meta,
            text_content="\n\n".join(parts),
            raw_ref=raw_ref,
            extra=extra,
        )


def get_jira_ingestion_source(
    jira_client: JiraClient | None = None,
) -> JiraIngestionSource:
    return JiraIngestionSource(jira_client=jira_client)
