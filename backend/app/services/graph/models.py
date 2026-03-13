"""
Canonical Pydantic v2 data contracts for the knowledge layer.

These models are the single source of truth for all artifacts, chunks, graph
edges, and ingestion runs stored under local_data/knowledge/.  Later phases
build on these contracts without changing the field definitions here.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class SourceSystem(str, Enum):
    """External system the artifact was ingested from."""

    JIRA = "jira"
    SHAREPOINT = "sharepoint"
    CONFLUENCE = "confluence"
    APPIAN = "appian"
    LOCAL = "local"
    UNKNOWN = "unknown"


class SourceType(str, Enum):
    """Structural type of the ingested source document."""

    TICKET = "ticket"
    DOCUMENT = "document"
    PAGE = "page"
    PROCESS = "process"
    SCREENSHOT = "screenshot"
    XML = "xml"
    UNKNOWN = "unknown"


class ArtifactKind(str, Enum):
    """Semantic kind of the normalised artifact."""

    EPIC = "epic"
    STORY = "story"
    TASK = "task"
    BUG = "bug"
    REQUIREMENT = "requirement"
    SPECIFICATION = "specification"
    MEETING_NOTES = "meeting_notes"
    DIAGRAM = "diagram"
    UNKNOWN = "unknown"


class ChunkType(str, Enum):
    """Structural type of a text chunk derived from an artifact."""

    FULL_TEXT = "full_text"
    PARAGRAPH = "paragraph"
    SECTION = "section"
    TABLE = "table"
    CODE_BLOCK = "code_block"
    HEADING = "heading"
    ACCEPTANCE_CRITERIA = "acceptance_criteria"
    UNKNOWN = "unknown"


class EdgeType(str, Enum):
    """Semantic type of a directed relationship between two artifacts/chunks."""

    RELATES_TO = "relates_to"
    DEPENDS_ON = "depends_on"
    BLOCKS = "blocks"
    IMPLEMENTS = "implements"
    REFERENCES = "references"
    CHILD_OF = "child_of"
    DUPLICATE_OF = "duplicate_of"
    SIMILAR_TO = "similar_to"


class IngestionStatus(str, Enum):
    """Lifecycle status of an ingestion run."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"


# ---------------------------------------------------------------------------
# Core models
# ---------------------------------------------------------------------------


class ArtifactMetadata(BaseModel):
    """Provenance and classification metadata for a single ingested artifact."""

    artifact_id: str
    source_type: SourceType
    source_system: SourceSystem
    external_id: str | None = None
    project_key: str | None = None
    title: str
    artifact_kind: ArtifactKind
    author: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    tags: list[str] = Field(default_factory=list)
    status: str | None = None
    url: str | None = None
    version: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    ingestion_run_id: str


class ArtifactRecord(BaseModel):
    """Normalised representation of a single ingested artifact."""

    metadata: ArtifactMetadata
    text_content: str
    summary: str | None = None
    raw_ref: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ChunkRecord(BaseModel):
    """A text chunk derived from an ArtifactRecord for downstream retrieval."""

    chunk_id: str
    artifact_id: str
    chunk_index: int
    chunk_type: ChunkType
    text: str
    token_estimate: int | None = None
    section_title: str | None = None
    page_or_location: str | None = None
    keywords: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    """A directed relationship between two artifacts or chunks in the knowledge graph."""

    edge_id: str
    from_id: str
    to_id: str
    edge_type: EdgeType
    source: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    evidence: str | None = None
    created_by: str = "system"
    ingestion_run_id: str | None = None


class IngestionRunStats(BaseModel):
    """Counters summarising the outcome of an ingestion run."""

    discovered: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    warnings: int = 0


class IngestionRun(BaseModel):
    """Record of a single ingestion run for one source connector."""

    run_id: str
    source_name: str
    source_type: SourceType
    status: IngestionStatus
    started_at: datetime
    completed_at: datetime | None = None
    stats: IngestionRunStats = Field(default_factory=IngestionRunStats)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    notes: str | None = None
