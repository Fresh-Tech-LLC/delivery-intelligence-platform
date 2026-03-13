"""
Abstract base class for all ingestion source connectors.

Each supported source system (Jira, SharePoint, Confluence, Appian, …) will
subclass BaseIngestionSource and implement fetch_artifacts() and health_check().
The IngestPipeline calls these methods and is agnostic to the source system.

Planned subclasses:
  - TODO Phase 1: JiraIngestionSource
  - TODO Phase 1: SharePointIngestionSource
  - TODO Phase 1: ConfluenceIngestionSource
  - TODO Phase 1: AppianIngestionSource
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from backend.app.services.graph.models import ArtifactRecord, SourceType


class BaseIngestionSource(ABC):
    """Abstract base for all ingestion source connectors."""

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Human-readable identifier for this source instance (e.g. 'my-jira')."""
        ...

    @property
    @abstractmethod
    def source_type(self) -> SourceType:
        """SourceType enum value that categorises this connector's output."""
        ...

    @abstractmethod
    def fetch_artifacts(self, run_id: str, **kwargs: Any) -> list[ArtifactRecord]:
        """Fetch artifacts from the source and return normalised ArtifactRecords.

        Args:
            run_id: The ingestion run ID to associate with each artifact.
            **kwargs: Source-specific filter or pagination parameters.

        Returns:
            List of ArtifactRecord objects ready for storage.
        """
        ...

    @abstractmethod
    def health_check(self) -> bool:
        """Return True if the source system is reachable and configured correctly."""
        ...
