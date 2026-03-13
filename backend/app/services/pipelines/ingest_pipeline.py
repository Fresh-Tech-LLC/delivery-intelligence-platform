"""
Ingest pipeline — orchestrates fetch → normalise → store for a single source connector.

Responsibilities (Phase 1 TODO):
  - Accept a BaseIngestionSource and an active IngestionRun ID.
  - Call source.fetch_artifacts(run_id) to retrieve normalised ArtifactRecords.
  - Persist each record via KnowledgeService.
  - Update the IngestionRun stats and status on completion.

Planned connector wiring:
  - TODO Phase 1: JiraIngestionSource
  - TODO Phase 1: SharePointIngestionSource
  - TODO Phase 1: ConfluenceIngestionSource
  - TODO Phase 1: AppianIngestionSource
"""
from __future__ import annotations

from typing import Any

from backend.app.services.ingestion.base import BaseIngestionSource


class IngestPipeline:
    """Scaffold — full implementation deferred to Phase 1."""

    def run(self, run_id: str, source: BaseIngestionSource, **kwargs: Any) -> None:
        """Run the ingestion pipeline for the given *source*.

        Args:
            run_id: Active IngestionRun ID to associate with fetched artifacts.
            source: Configured source connector to fetch from.
            **kwargs: Passed through to source.fetch_artifacts().
        """
        # TODO Phase 1: call source.fetch_artifacts(run_id, **kwargs)
        # TODO Phase 1: save each ArtifactRecord via KnowledgeService.save_artifact()
        # TODO Phase 1: update IngestionRun stats and mark status COMPLETED / PARTIAL / FAILED
        raise NotImplementedError
