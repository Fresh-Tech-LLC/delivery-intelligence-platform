"""
Refresh pipeline — detects stale artifacts and schedules re-ingestion.

Responsibilities (Phase 4 TODO):
  - Compare ArtifactMetadata.updated_at against the source system's last-modified
    timestamp to identify stale records.
  - Trigger IngestPipeline for stale artifacts.
  - Update IngestionRun stats with discovered / updated / skipped counts.

Planned work:
  - TODO Phase 4: implement per-source staleness checks using updated_at
  - TODO Phase 4: implement incremental Jira JQL queries for recent changes
  - TODO Phase 4: support a configurable max_age_hours threshold
"""
from __future__ import annotations

from typing import Any


class RefreshPipeline:
    """Scaffold — full implementation deferred to Phase 4."""

    def run(self, run_id: str, **kwargs: Any) -> None:
        """Identify and re-ingest stale artifacts.

        Args:
            run_id: Active IngestionRun ID for the refresh job.
            **kwargs: Future options (max_age_hours, source_filter, …).
        """
        # TODO Phase 4: load all ArtifactRecords via KnowledgeService.list_artifacts()
        # TODO Phase 4: check each artifact's updated_at against source system
        # TODO Phase 4: trigger IngestPipeline.run() for stale artifacts
        # TODO Phase 4: update IngestionRun stats
        raise NotImplementedError
