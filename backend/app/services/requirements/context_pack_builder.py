"""Context pack assembly for Requirements Studio."""
from __future__ import annotations

from datetime import datetime, timezone
import logging

from backend.app.config import get_settings
from backend.app.services.graph.models import ArtifactRecord, ChunkRecord
from backend.app.services.knowledge_service import KnowledgeService
from backend.app.services.requirements.models import (
    ContextPack,
    ContextRelatedHit,
    ContextSearchHit,
    FeatureWorkspace,
)

logger = logging.getLogger(__name__)


class ContextPackBuilder:
    """Build deterministic context packs from existing knowledge services."""

    def __init__(self, service: KnowledgeService) -> None:
        self._service = service
        self._settings = get_settings()

    def build(self, workspace: FeatureWorkspace) -> ContextPack:
        query_text = " ".join(part.strip() for part in [workspace.title, workspace.request_text] if part.strip())
        warnings: list[str] = []
        search_hits: list[ContextSearchHit] = []
        related_hits: list[ContextRelatedHit] = []
        selected_source_refs: list[str] = []

        try:
            search_results = self._service.search_chunks(
                q=query_text,
                project_key=workspace.project_key,
                limit=self._settings.requirements_context_max_search_results,
            )
        except ValueError as exc:
            warnings.append(str(exc))
            search_results = []

        seen_refs: set[str] = set()
        top_artifact_ids: list[str] = []
        for hit in search_results:
            ref_id = str(hit["chunk_id"])
            if ref_id in seen_refs:
                continue
            seen_refs.add(ref_id)
            artifact_info = hit.get("artifact", {})
            search_hits.append(
                ContextSearchHit(
                    ref_id=ref_id,
                    artifact_id=str(hit["artifact_id"]),
                    title=str(artifact_info.get("title") or hit["artifact_id"]),
                    source_system=self._as_optional_str(artifact_info.get("source_system")),
                    artifact_kind=self._as_optional_str(artifact_info.get("artifact_kind")),
                    snippet=str(hit.get("snippet") or ""),
                    score=float(hit.get("score") or 0.0),
                    metadata={
                        "matched_terms": list(hit.get("matched_terms", [])),
                        "section_title": hit.get("section_title"),
                        "chunk_index": hit.get("chunk_index"),
                    },
                )
            )
            artifact_id = str(hit["artifact_id"])
            if artifact_id not in top_artifact_ids:
                top_artifact_ids.append(artifact_id)
            selected_source_refs.append(ref_id)

        max_related = max(0, self._settings.requirements_context_max_related_results)
        for artifact_id in top_artifact_ids[:max_related]:
            try:
                related_results = self._service.get_related_artifacts(
                    artifact_id,
                    limit=max_related,
                )
            except ValueError as exc:
                warnings.append(str(exc))
                continue
            for item in related_results:
                if len(related_hits) >= max_related:
                    break
                ref_id = str(item["artifact_id"])
                if ref_id in seen_refs:
                    continue
                seen_refs.add(ref_id)
                related_hits.append(
                    ContextRelatedHit(
                        ref_id=ref_id,
                        title=str(item.get("title") or ref_id),
                        source_system=self._as_optional_str(item.get("source_system")),
                        artifact_kind=self._as_optional_str(item.get("artifact_kind")),
                        edge_types=[str(edge_type) for edge_type in item.get("edge_types", [])],
                        score=float(item.get("score") or 0.0),
                        rationale=self._as_optional_str(item.get("rationale")),
                        metadata={},
                    )
                )
                selected_source_refs.append(ref_id)
            if len(related_hits) >= max_related:
                break

        for item in workspace.pinned_evidence:
            if item.ref_id not in selected_source_refs:
                selected_source_refs.append(item.ref_id)

        if not search_hits:
            warnings.append("No lexical search hits were found for this workspace.")
        if not related_hits:
            warnings.append("No related artifacts were found from the current context.")

        summary_text = self._build_summary(workspace, search_hits, related_hits)
        return ContextPack(
            workspace_id=workspace.workspace_id,
            query_text=query_text,
            search_hits=search_hits,
            related_hits=related_hits,
            pinned_items=list(workspace.pinned_evidence),
            selected_source_refs=list(dict.fromkeys(selected_source_refs)),
            summary_text=summary_text,
            warnings=list(dict.fromkeys(warnings)),
            built_at=datetime.now(timezone.utc),
        )

    def load_ref_text(self, ref_id: str) -> str:
        """Return a bounded text snippet for an artifact or chunk ref."""
        chunk = self._service.get_chunk(ref_id)
        if isinstance(chunk, ChunkRecord):
            return chunk.text[:1200]
        artifact = self._service.get_artifact(ref_id)
        if isinstance(artifact, ArtifactRecord):
            return artifact.text_content[:1200]
        return ""

    def _build_summary(
        self,
        workspace: FeatureWorkspace,
        search_hits: list[ContextSearchHit],
        related_hits: list[ContextRelatedHit],
    ) -> str:
        lines = [
            f"Workspace '{workspace.title}' context built from {len(search_hits)} search hits, "
            f"{len(related_hits)} related artifacts, and {len(workspace.pinned_evidence)} pinned items."
        ]
        if search_hits:
            top = search_hits[0]
            lines.append(f"Top evidence: {top.title} ({top.ref_id}) score={top.score:.2f}.")
        if related_hits:
            top_related = related_hits[0]
            lines.append(
                f"Top related artifact: {top_related.title} ({top_related.ref_id}) "
                f"via {', '.join(top_related.edge_types[:3]) or 'graph links'}."
            )
        return " ".join(lines)

    def _as_optional_str(self, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None
