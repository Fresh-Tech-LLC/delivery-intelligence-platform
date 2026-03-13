# ADR 0001 — Knowledge Foundation Contracts (Phase 0)

## Status

Accepted

## Context

The Delivery Intelligence Platform needs to ingest artifacts from multiple source systems —
Jira tickets, SharePoint documents, Confluence pages, Appian XML process definitions,
screenshots, and uploaded files — into a normalised internal model. Future work requires
semantic search, graph-based dependency analysis, and LLM-assisted reasoning over this
ingested content.

Before any ingestion logic is implemented, the team needs stable data contracts so that:
- Later phases can build on a known schema without retroactive migrations.
- The storage layer can be swapped (file-based → vector DB → graph DB) without touching
  the contracts themselves.
- The rest of the application (routers, agents) has a single facade to talk to.

## Decision

Phase 0 implements the foundational contracts using the following approach:

**Canonical Pydantic v2 models** (`backend/app/services/graph/models.py`):
- `ArtifactRecord` — a single normalised artifact from any source system.
- `ChunkRecord` — a text chunk derived from an artifact for downstream retrieval.
- `GraphEdge` — a directed relationship between two artifacts or chunks.
- `IngestionRun` — a record of a single ingestion job and its outcome stats.

**File-based JSON persistence** (`local_data/knowledge/`):
- One JSON file per record, named by its primary ID.
- Atomic writes via a temp-file-then-replace pattern (matches existing project patterns).
- Directories created automatically on first write.
- No database, no queue, no new infrastructure dependencies.

**Graph-style relationships without a database**:
- `GraphEdge` objects are stored as plain JSON files.
- Edges reference artifact/chunk IDs by string; no foreign-key constraints.
- This is sufficient for Phase 0 and Phase 1; a graph DB can be adopted later when
  traversal and query patterns are well understood.

**Thin service facade** (`backend/app/services/knowledge_service.py`):
- Single import surface for routers and future pipeline code.
- Delegates all I/O to the individual store classes.

**Read-only API routes** (`/api/knowledge/*`):
- Safe for local development; no write endpoints exposed in Phase 0.
- Includes a `/bootstrap` endpoint to seed deterministic sample data for validation.

## Consequences

**Positive:**
- Zero infrastructure dependencies — runs entirely on the local filesystem.
- Easy to iterate on contracts before any real ingestion logic is committed.
- New source connectors can be added without touching existing code.
- File-based storage is transparent and inspectable with any text editor.

**Negative / deferred:**
- File-based storage does not support efficient querying or vector similarity search.
  A migration to a vector store (e.g. Chroma, pgvector) or graph DB (e.g. Neo4j) will
  be needed in a later phase when query patterns are known.
- No indexing — `list_all()` scans every JSON file in the directory.  Acceptable for
  MVP but will need a manifest or index file for larger datasets.
- No schema versioning — if models change, existing JSON files must be migrated manually.
  This is deferred until the schema stabilises after Phase 2.
