# Knowledge Layer — Phase 1

Phase 1 wires up the first real data flow: fetching Jira issues and scanning local
filesystem documents, normalising both into `ArtifactRecord` objects using the Phase 0
contracts, and exposing POST trigger endpoints.

No new infrastructure dependencies are introduced (`python-docx` and `openpyxl` were
already in `requirements.txt`). Embeddings, graph linking, and vector search remain deferred.

---

## What Phase 1 Adds

| Component | Purpose |
|-----------|---------|
| `ingestion/raw_store.py` | Saves pre-normalisation payloads to `raw/jira/` and `raw/local/` |
| `parsers/text_parser.py` | Extracts text from `.txt` and `.md` files |
| `parsers/docx_parser.py` | Extracts paragraphs and tables from `.docx` files |
| `parsers/xlsx_parser.py` | Extracts sheet data from `.xlsx` files |
| `ingestion/jira_ingestor.py` | `JiraIngestionSource` — fetches and normalises Jira issues |
| `ingestion/local_docs_ingestor.py` | `LocalDocsIngestionSource` — scans and normalises local files |
| `pipelines/ingest_pipeline.py` | Real orchestrator replacing the Phase 0 stub |
| `schemas_knowledge.py` | Pydantic request models for Phase 1 endpoints |
| `knowledge_service.py` (modified) | `run_jira_ingestion`, `run_local_docs_ingestion`, `search_artifacts` |
| `routers/knowledge.py` (modified) | `POST /ingest/jira`, `POST /ingest/local-docs`, `GET /artifacts/search` |

---

## New Folder Tree

```
backend/app/
├── schemas_knowledge.py                               [NEW]
└── services/
    ├── ingestion/
    │   ├── jira_ingestor.py                           [NEW]
    │   ├── local_docs_ingestor.py                     [NEW]
    │   └── raw_store.py                               [NEW]
    └── parsers/
        ├── __init__.py                                [NEW]
        ├── text_parser.py                             [NEW]
        ├── docx_parser.py                             [NEW]
        └── xlsx_parser.py                             [NEW]
docs/
├── adr/
│   └── 0002-phase1-raw-ingestion.md                  [NEW]
└── knowledge_phase1.md                               ← this file
```

---

## Jira Normalisation

### JQL Resolution Priority

| Priority | Source | Condition |
|----------|--------|-----------|
| 1 | Request body `jql` field | Always takes precedence if provided |
| 2 | Request body `project_key` | Builds `project = "{key}" ORDER BY updated DESC` |
| 3 | `KNOWLEDGE_DEFAULT_PROJECT_KEY` in `.env` | Fallback project key |
| 4 | `JIRA_PROJECT_KEY` in `.env` | Existing Jira config — no new setting needed |
| — | None found | 400 Bad Request |

### Field Mapping

| Jira Field | ArtifactMetadata Field | Notes |
|-----------|----------------------|-------|
| `key` | `artifact_id` = `jira-{key.lower()}` | Stable, deterministic |
| `fields.summary` | `title` | |
| `fields.issuetype.name` | `artifact_kind` | Via `_KIND_MAP` |
| `fields.status.name` | `status` | |
| `fields.reporter.displayName` | `author` | Falls back to assignee |
| `fields.created` | `created_at` | Parsed from Jira timestamp |
| `fields.updated` | `updated_at` | |
| `fields.labels` | `tags` | |
| `{base_url}/browse/{key}` | `url` | |

### ArtifactKind Mapping

| Jira issue type | ArtifactKind |
|-----------------|-------------|
| epic | `EPIC` |
| story | `STORY` |
| task, sub-task, subtask | `TASK` |
| bug, defect | `BUG` |
| requirement | `REQUIREMENT` |
| specification | `SPECIFICATION` |
| _(anything else)_ | `UNKNOWN` |

### Artifact ID Convention

```
jira-{issue_key.lower()}

Examples:
  PROJ-123  →  jira-proj-123
  ABC-42    →  jira-abc-42
```

---

## Local Docs Normalisation

### Supported Extensions

`.txt`, `.md`, `.docx`, `.xlsx`

Files exceeding `KNOWLEDGE_MAX_FILE_BYTES` (default 20 MB) are skipped with a warning.

### Artifact ID Sanitisation

```python
# path relative to scan root, suffix removed, non-word chars → "-", lowercased
local-{sanitized_relative_path}

Examples:
  requirements/req-001.txt  →  local-requirements-req-001
  design notes.docx         →  local-design-notes
```

### Default ArtifactKind

All local documents default to `ArtifactKind.SPECIFICATION`. This can be refined in
Phase 2 (chunking) once content patterns are better understood.

---

## Raw Storage Layout

```
local_data/knowledge/raw/
├── jira/
│   └── {run_id}/
│       └── {issue_key}.json    # Raw Jira REST API response
└── local/
    └── {run_id}/
        └── {artifact_id}{ext}  # Copy of the original file
```

Set `KNOWLEDGE_RAW_CAPTURE_ENABLED=false` in `.env` to disable raw capture.

---

## Endpoint Usage

### Trigger Jira ingestion

```bash
# Use default project key from .env
curl -X POST http://localhost:8000/api/knowledge/ingest/jira \
     -H "Content-Type: application/json" \
     -d '{}'

# Specify project key and limit
curl -X POST http://localhost:8000/api/knowledge/ingest/jira \
     -H "Content-Type: application/json" \
     -d '{"project_key": "PROJ", "max_results": 50}'

# Raw JQL query
curl -X POST http://localhost:8000/api/knowledge/ingest/jira \
     -H "Content-Type: application/json" \
     -d '{"jql": "project = PROJ AND issuetype = Story AND updated >= -7d"}'
```

### Trigger local document ingestion

```bash
# Scan default KNOWLEDGE_LOCAL_DOCS_DIR
curl -X POST http://localhost:8000/api/knowledge/ingest/local-docs \
     -H "Content-Type: application/json" \
     -d '{}'

# Scan a specific directory with a project key tag
curl -X POST http://localhost:8000/api/knowledge/ingest/local-docs \
     -H "Content-Type: application/json" \
     -d '{"root_dir": "local_data/my_docs", "project_key": "PROJ", "recursive": true}'
```

### Search artifacts

```bash
GET /api/knowledge/artifacts/search?source_system=jira
GET /api/knowledge/artifacts/search?source_system=local
GET /api/knowledge/artifacts/search?artifact_kind=story
GET /api/knowledge/artifacts/search?project_key=PROJ&title_contains=login
```

Query parameters are all optional and ANDed together.

---

## How to Test Phase 1

### 1. Start the server

```bash
uvicorn backend.app.main:app --reload
```

### 2. Confirm Phase 0 routes still work

```bash
curl http://localhost:8000/api/knowledge/health
# → {"status":"ok","storage":"file-based","layer":"knowledge","phase":0}
```

### 3. Test Jira ingestion

Requires `JIRA_BASE_URL`, `JIRA_USER`, `JIRA_API_TOKEN`, and `JIRA_PROJECT_KEY` in `.env`.

```bash
curl -s -X POST http://localhost:8000/api/knowledge/ingest/jira \
     -H "Content-Type: application/json" \
     -d '{"max_results": 5}' | python -m json.tool
```

Expected response shape:
```json
{
  "run_id": "...",
  "status": "completed",
  "stats": {"discovered": 5, "created": 5, "updated": 0, "failed": 0},
  ...
}
```

Re-run to verify idempotence — `created` should drop to 0 and `updated` should equal the
discovered count.

### 4. Test local document ingestion

```bash
mkdir -p local_data/knowledge_docs
echo "This is a sample requirement." > local_data/knowledge_docs/req-001.txt

curl -s -X POST http://localhost:8000/api/knowledge/ingest/local-docs \
     -H "Content-Type: application/json" \
     -d '{"recursive": true}' | python -m json.tool
```

### 5. Verify files on disk

```bash
ls local_data/knowledge/normalized/artifacts/   # jira-*.json and local-*.json
ls local_data/knowledge/runs/                   # one run JSON per trigger
ls local_data/knowledge/raw/jira/               # raw Jira API payloads per run
ls local_data/knowledge/raw/local/              # file copies per run
```

### 6. Search artifacts

```bash
curl "http://localhost:8000/api/knowledge/artifacts/search?source_system=jira"
curl "http://localhost:8000/api/knowledge/artifacts/search?source_system=local"
curl "http://localhost:8000/api/knowledge/artifacts/search?artifact_kind=story&title_contains=login"
```

### 7. Verify existing routes are unaffected

```bash
GET http://localhost:8000/          → home page renders
GET http://localhost:8000/ba/requirements → BA page renders
```

---

## How Future Phases Build On This

| Phase | What it adds |
|-------|-------------|
| **Phase 2 — Chunk** | `ChunkPipeline.run()` splits `ArtifactRecord.text_content` into `ChunkRecord` objects |
| **Phase 3 — Link** | `LinkPipeline.run()` derives `GraphEdge` objects from Jira issue links and semantic similarity |
| **Phase 4 — Refresh** | `RefreshPipeline.run()` detects stale artifacts and triggers re-ingestion |
| **Phase 5 — Retrieval** | Vector embeddings added to `ChunkRecord.extra`; storage migrated to a vector store |
