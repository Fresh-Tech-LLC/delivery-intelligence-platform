"""
Ingest pipeline — orchestrates fetch → normalise → store for one ingestion run.

Responsibilities:
  - Transition the IngestionRun through PENDING → RUNNING → COMPLETED/PARTIAL/FAILED.
  - Call source.fetch_artifacts() to retrieve normalised ArtifactRecord objects.
  - Persist each artifact via KnowledgeService, tracking created/updated/failed counts.
  - Return the final IngestionRun for the caller to inspect or return to the API.

Circular import resolution:
  KnowledgeService is referenced only as a type annotation (TYPE_CHECKING guard).
  With `from __future__ import annotations` all annotations are strings at runtime,
  so the import is never evaluated outside of type-checking tools.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from backend.app.services.graph.models import IngestionRun, IngestionStatus
from backend.app.services.ingestion.base import BaseIngestionSource

if TYPE_CHECKING:
    from backend.app.services.knowledge_service import KnowledgeService

logger = logging.getLogger(__name__)


class IngestPipeline:
    """Orchestrates a single ingestion run from source fetch to artifact persistence."""

    def run(
        self,
        run_id: str,
        source: BaseIngestionSource,
        service: KnowledgeService,
        **kwargs: Any,
    ) -> IngestionRun:
        """Execute the ingestion pipeline for *run_id*.

        Args:
            run_id: ID of an existing PENDING IngestionRun created by KnowledgeService.
            source: Concrete ingestion source (e.g. JiraIngestionSource).
            service: KnowledgeService facade used for all persistence.
            **kwargs: Forwarded to source.fetch_artifacts() (project_key, jql, etc.).

        Returns:
            The updated IngestionRun with final status and stats.
        """
        run = service.get_run(run_id)
        if run is None:
            raise ValueError(f"IngestionRun '{run_id}' not found")

        run.status = IngestionStatus.RUNNING
        service.save_run(run)

        try:
            artifacts = source.fetch_artifacts(run_id, **kwargs)
        except Exception as exc:
            run.status = IngestionStatus.FAILED
            run.completed_at = datetime.now(timezone.utc)
            run.errors.append(str(exc))
            service.save_run(run)
            return run

        run.stats.discovered = len(artifacts)

        for artifact in artifacts:
            try:
                existing = service.get_artifact(artifact.metadata.artifact_id)
                service.save_artifact(artifact)
                if existing is None:
                    run.stats.created += 1
                else:
                    run.stats.updated += 1
            except Exception as exc:
                logger.warning(
                    "IngestPipeline: failed to save %s (%s)",
                    artifact.metadata.artifact_id,
                    exc,
                )
                run.stats.failed += 1

        run.completed_at = datetime.now(timezone.utc)
        if run.stats.failed == 0:
            run.status = IngestionStatus.COMPLETED
        elif run.stats.failed < run.stats.discovered:
            run.status = IngestionStatus.PARTIAL
        else:
            run.status = IngestionStatus.FAILED

        service.save_run(run)
        return run


def get_ingest_pipeline() -> IngestPipeline:
    return IngestPipeline()
