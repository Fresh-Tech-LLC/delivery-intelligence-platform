# Knowledge Layer — Phase 0

Phase 0 establishes the foundational contracts, storage layer, service facade, and API routes
for the knowledge ingestion workstream.  It does **not** implement actual ingestion logic —
that begins in Phase 1.

---

## What Phase 0 Includes

| Component | Purpose |
|-----------|---------|
| `graph/models.py` | Canonical Pydantic v2 contracts for all knowledge objects |
| `graph/paths.py` | Centralised path helpers and atomic write utility |
| `graph/artifact_store.py` | File-based CRUD for `ArtifactRecord` |
| `graph/chunk_store.py` | File-based CRUD for `ChunkRecord` |
| `graph/edge_store.py` | File-based CRUD for `GraphEdge` |
| `ingestion/run_store.py` | File-based CRUD for `IngestionRun` |
| `ingestion/base.py` | Abstract base class for source connectors |
| `pipelines/ingest_pipeline.py` | Scaffold for fetch → store orchestration |
| `pipelines/chunk_pipeline.py` | Scaffold for text splitting |
| `pipelines/link_pipeline.py` | Scaffold for graph edge derivation |
| `pipelines/refresh_pipeline.py` | Scaffold for staleness detection |
| `knowledge_service.py` | Thin facade over all stores |
| `routers/knowledge.py` | Read-only `/api/knowledge/*` endpoints |

---

## New Folder Tree

```
backend/app/
├── routers/
│   └── knowledge.py
└── services/
    ├── knowledge_service.py
    ├── graph/
    │   ├── __init__.py
    │   ├── models.py
    │   ├── paths.py
    │   ├── artifact_store.py
    │   ├── chunk_store.py
    │   └── edge_store.py
    ├── ingestion/
    │   ├── __init__.py
    │   ├── base.py
    │   └── run_store.py
    └── pipelines/
        ├── __init__.py
        ├── ingest_pipeline.py
        ├── chunk_pipeline.py
        ├── link_pipeline.py
        └── refresh_pipeline.py
docs/
├── adr/
│   └── 0001-knowledge-foundation-contracts.md
└── knowledge_phase0.md  ← this file
```

---

## Model Purpose

| Model | Description |
|-------|-------------|
| `ArtifactMetadata` | Provenance fields: source system, kind, author, timestamps, project key, run ID |
| `ArtifactRecord` | Full normalised artifact: metadata + text content + optional summary |
| `ChunkRecord` | Text chunk derived from an artifact: index, type, text, keywords, entities |
| `GraphEdge` | Directed relationship between two artifact/chunk IDs with confidence and evidence |
| `IngestionRunStats` | Counters for discovered / created / updated / skipped / failed per run |
| `IngestionRun` | One ingestion job: source, status, timestamps, stats, warnings, errors |

---

## Storage Layout

```
local_data/knowledge/
├── raw/                              # Reserved for raw source dumps (Phase 1)
├── normalized/
│   └── artifacts/
│       └── {artifact_id}.json       # One ArtifactRecord per file
├── chunks/
│   └── {chunk_id}.json              # One ChunkRecord per file
├── edges/
│   └── {edge_id}.json               # One GraphEdge per file
└── runs/
    └── {run_id}.json                # One IngestionRun per file
```

All files are UTF-8 JSON with 2-space indentation.  Writes are atomic (temp file → replace).
Directories are created automatically on first write.

---

## How Future Phases Build On This

| Phase | What it adds |
|-------|-------------|
| **Phase 1 — Ingest** | `JiraIngestionSource`, `SharePointIngestionSource`, etc. implement `BaseIngestionSource`; `IngestPipeline.run()` is wired up |
| **Phase 2 — Chunk** | `ChunkPipeline.run()` splits artifact text, estimates tokens, extracts keywords/entities |
| **Phase 3 — Link** | `LinkPipeline.run()` derives `GraphEdge` objects from Jira links and semantic similarity |
| **Phase 4 — Refresh** | `RefreshPipeline.run()` detects stale artifacts and triggers re-ingestion |
| **Phase 5 — Retrieval** | Vector embeddings added to `ChunkRecord.extra`; storage migrated to a vector store |

---

## How to Test Phase 0

### 1. Start the server

```bash
uvicorn backend.app.main:app --reload
```

### 2. Seed sample data

```
GET http://localhost:8000/api/knowledge/bootstrap
```

Expected response:
```json
{
  "run_id": "sample-run-001",
  "artifact_id": "sample-artifact-001",
  "chunk_id": "sample-chunk-001",
  "edge_id": "sample-edge-001"
}
```

### 3. Verify files on disk

```
local_data/knowledge/runs/sample-run-001.json
local_data/knowledge/normalized/artifacts/sample-artifact-001.json
local_data/knowledge/chunks/sample-chunk-001.json
local_data/knowledge/edges/sample-edge-001.json
```

### 4. Query the API

```
GET /api/knowledge/health
GET /api/knowledge/artifacts
GET /api/knowledge/artifacts/sample-artifact-001
GET /api/knowledge/chunks
GET /api/knowledge/chunks?artifact_id=sample-artifact-001
GET /api/knowledge/edges
GET /api/knowledge/runs
```

All endpoints return JSON.  The `/bootstrap` endpoint is idempotent — calling it again
overwrites the same fixed sample IDs.

### 5. Verify existing routes are unaffected

```
GET http://localhost:8000/          → home page renders
GET http://localhost:8000/ba/requirements → BA page renders
```
