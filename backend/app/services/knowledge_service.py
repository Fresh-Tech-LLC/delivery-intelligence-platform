"""
Thin service facade over the knowledge stores.

This is the single import surface for routers and future pipeline code.
All persistence is delegated to the individual store classes; this facade
adds no business logic of its own.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from backend.app.services.graph.artifact_store import get_artifact_store
from backend.app.services.graph.chunk_store import get_chunk_store
from backend.app.services.graph.edge_store import get_edge_store
from backend.app.services.ingestion.run_store import get_run_store
from backend.app.services.graph.models import (
    ArtifactKind,
    ArtifactMetadata,
    ArtifactRecord,
    ChunkRecord,
    ChunkType,
    EdgeType,
    GraphEdge,
    IngestionRun,
    IngestionRunStats,
    IngestionStatus,
    SourceSystem,
    SourceType,
)


class KnowledgeService:
    """Facade over the four knowledge stores (artifacts, chunks, edges, runs)."""

    def __init__(self) -> None:
        self._artifacts = get_artifact_store()
        self._chunks = get_chunk_store()
        self._edges = get_edge_store()
        self._runs = get_run_store()

    # ------------------------------------------------------------------
    # Ingestion runs
    # ------------------------------------------------------------------

    def create_run(self, source_name: str, source_type: SourceType) -> IngestionRun:
        """Create and persist a new PENDING IngestionRun. Returns the saved run."""
        run = IngestionRun(
            run_id=uuid.uuid4().hex,
            source_name=source_name,
            source_type=source_type,
            status=IngestionStatus.PENDING,
            started_at=datetime.now(timezone.utc),
        )
        self._runs.save(run)
        return run

    def save_run(self, run: IngestionRun) -> None:
        """Persist an updated IngestionRun (e.g. after marking COMPLETED)."""
        self._runs.save(run)

    def get_run(self, run_id: str) -> IngestionRun | None:
        return self._runs.get(run_id)

    def list_runs(self) -> list[IngestionRun]:
        return self._runs.list_all()

    # ------------------------------------------------------------------
    # Artifacts
    # ------------------------------------------------------------------

    def save_artifact(self, record: ArtifactRecord) -> None:
        self._artifacts.save(record)

    def get_artifact(self, artifact_id: str) -> ArtifactRecord | None:
        return self._artifacts.get(artifact_id)

    def list_artifacts(self) -> list[ArtifactRecord]:
        return self._artifacts.list_all()

    # ------------------------------------------------------------------
    # Chunks
    # ------------------------------------------------------------------

    def save_chunk(self, record: ChunkRecord) -> None:
        self._chunks.save(record)

    def get_chunk(self, chunk_id: str) -> ChunkRecord | None:
        return self._chunks.get(chunk_id)

    def list_chunks(self, artifact_id: str | None = None) -> list[ChunkRecord]:
        """Return chunks, optionally filtered to a single *artifact_id*."""
        if artifact_id:
            return self._chunks.list_by_artifact(artifact_id)
        return self._chunks.list_all()

    # ------------------------------------------------------------------
    # Edges
    # ------------------------------------------------------------------

    def save_edge(self, edge: GraphEdge) -> None:
        self._edges.save(edge)

    def get_edge(self, edge_id: str) -> GraphEdge | None:
        return self._edges.get(edge_id)

    def list_edges(self) -> list[GraphEdge]:
        return self._edges.list_all()

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------

    def bootstrap_sample_data(self) -> dict[str, str]:
        """Create deterministic sample records for Phase 0 local validation.

        Idempotent — calling multiple times overwrites the same fixed IDs.
        Returns a dict of the created IDs for inspection.
        """
        run = IngestionRun(
            run_id="sample-run-001",
            source_name="sample",
            source_type=SourceType.DOCUMENT,
            status=IngestionStatus.COMPLETED,
            started_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
            completed_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
            stats=IngestionRunStats(discovered=1, created=1),
            notes="Phase 0 bootstrap sample",
        )
        meta = ArtifactMetadata(
            artifact_id="sample-artifact-001",
            source_type=SourceType.DOCUMENT,
            source_system=SourceSystem.LOCAL,
            title="Sample Requirement",
            artifact_kind=ArtifactKind.REQUIREMENT,
            ingestion_run_id="sample-run-001",
        )
        artifact = ArtifactRecord(
            metadata=meta,
            text_content="This is a sample requirement for Phase 0 validation.",
            summary="Sample requirement summary.",
        )
        chunk = ChunkRecord(
            chunk_id="sample-chunk-001",
            artifact_id="sample-artifact-001",
            chunk_index=0,
            chunk_type=ChunkType.FULL_TEXT,
            text="This is a sample requirement for Phase 0 validation.",
        )
        edge = GraphEdge(
            edge_id="sample-edge-001",
            from_id="sample-artifact-001",
            to_id="sample-artifact-001",
            edge_type=EdgeType.RELATES_TO,
            source="bootstrap",
            evidence="Self-referential sample edge for Phase 0 validation.",
            ingestion_run_id="sample-run-001",
        )
        self._runs.save(run)
        self._artifacts.save(artifact)
        self._chunks.save(chunk)
        self._edges.save(edge)
        return {
            "run_id": run.run_id,
            "artifact_id": meta.artifact_id,
            "chunk_id": chunk.chunk_id,
            "edge_id": edge.edge_id,
        }


    # ------------------------------------------------------------------
    # Phase 1 ingestion triggers
    # ------------------------------------------------------------------

    def run_jira_ingestion(
        self,
        project_key: str | None = None,
        jql: str | None = None,
        max_results: int | None = None,
    ) -> IngestionRun:
        """Trigger a Jira ingestion run and return the completed IngestionRun."""
        # Deferred imports prevent circular import (KnowledgeService ↔ IngestPipeline)
        from backend.app.services.ingestion.jira_ingestor import get_jira_ingestion_source
        from backend.app.services.pipelines.ingest_pipeline import get_ingest_pipeline

        source = get_jira_ingestion_source()
        run = self.create_run(source.source_name, source.source_type)
        kwargs: dict[str, Any] = {}
        if project_key:
            kwargs["project_key"] = project_key
        if jql:
            kwargs["jql"] = jql
        if max_results is not None:
            kwargs["max_results"] = max_results
        return get_ingest_pipeline().run(run.run_id, source, self, **kwargs)

    def run_local_docs_ingestion(
        self,
        root_dir: str | None = None,
        project_key: str | None = None,
        recursive: bool = True,
    ) -> IngestionRun:
        """Trigger a local document ingestion run and return the completed IngestionRun."""
        from backend.app.services.ingestion.local_docs_ingestor import get_local_docs_ingestion_source
        from backend.app.services.pipelines.ingest_pipeline import get_ingest_pipeline

        source = get_local_docs_ingestion_source()
        run = self.create_run(source.source_name, source.source_type)
        kwargs: dict[str, Any] = {"recursive": recursive}
        if root_dir:
            kwargs["root_dir"] = root_dir
        if project_key:
            kwargs["project_key"] = project_key
        return get_ingest_pipeline().run(run.run_id, source, self, **kwargs)

    def search_artifacts(
        self,
        project_key: str | None = None,
        source_system: SourceSystem | None = None,
        artifact_kind: ArtifactKind | None = None,
        title_contains: str | None = None,
    ) -> list[ArtifactRecord]:
        """Return artifacts matching the given filters (all optional, ANDed together)."""
        results = self._artifacts.list_all()
        if project_key:
            results = [r for r in results if r.metadata.project_key == project_key]
        if source_system:
            results = [r for r in results if r.metadata.source_system == source_system]
        if artifact_kind:
            results = [r for r in results if r.metadata.artifact_kind == artifact_kind]
        if title_contains:
            needle = title_contains.lower()
            results = [r for r in results if needle in r.metadata.title.lower()]
        return results


def get_knowledge_service() -> KnowledgeService:
    return KnowledgeService()
