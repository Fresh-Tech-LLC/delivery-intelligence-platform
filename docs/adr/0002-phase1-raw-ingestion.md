# ADR 0002 — Phase 1: Raw Ingestion and Normalisation

## Status

Accepted

## Context

Phase 0 established the canonical Pydantic v2 contracts, file-based stores, and read-only
`/api/knowledge/*` endpoints. Before semantic search (Phase 5) or graph linking (Phase 3)
can be built, the platform needs real artifact data flowing in from actual source systems.

Phase 1 implements the first two source connectors — Jira tickets and local filesystem
documents — and the orchestration layer that runs them on demand.

## Decisions

### D1: Reuse `JiraClient` — no duplicate auth logic

`JiraIngestionSource` calls `get_jira_client()` and delegates all HTTP requests to the
existing `JiraClient`. Auth config, TLS settings, and retry logic are already covered.
Adding a second HTTP client for Jira would create dual maintenance burden.

### D2: Non-fatal raw capture with `KNOWLEDGE_RAW_CAPTURE_ENABLED` flag

`RawStore` saves pre-normalisation payloads to `local_data/knowledge/raw/` before each
artifact is processed. Write failures are logged as warnings and do not abort ingestion.
This provides an audit trail and enables offline re-normalisation without re-fetching.
The flag allows disabling raw capture in environments with storage constraints.

### D3: Extension-dispatched pure-function parsers with deferred imports

Each parser (`text_parser`, `docx_parser`, `xlsx_parser`) exposes a single function
`extract_text(path) -> (str, dict)`. The dispatcher in `local_docs_ingestor` uses
function-scope imports so `python-docx` and `openpyxl` are only loaded when a matching
file is actually encountered, keeping startup fast.

### D4: Deterministic artifact IDs for idempotent re-runs

- Jira: `jira-{issue_key.lower()}` (e.g. `jira-proj-123`)
- Local docs: `local-{sanitized_relative_path}` (e.g. `local-requirements-req-001`)

Re-running the same ingestion job updates existing records rather than creating duplicates.
`KnowledgeService.get_artifact()` is used to distinguish created vs. updated in run stats.

### D5: Circular import resolution via `TYPE_CHECKING` guard and deferred method imports

`IngestPipeline.run()` takes a `KnowledgeService` argument. To avoid a circular import
at module load time:
1. `ingest_pipeline.py` imports `KnowledgeService` only under `TYPE_CHECKING` — the
   annotation is never evaluated at runtime (Python's `from __future__ import annotations`
   makes all annotations lazy strings).
2. `knowledge_service.py` imports ingestors and the pipeline inside method bodies —
   executed only when the method is called, not at module initialisation.

### D6: `jira_project_key` as third-tier JQL fallback

JQL resolution priority in `JiraIngestionSource`:
1. `kwargs["jql"]` — explicit raw JQL from the caller
2. `kwargs["project_key"]` → `settings.knowledge_default_project_key` — request-level or
   env-level project override
3. `settings.jira_project_key` — the existing Jira config field already used by other parts
   of the application

This means existing `.env` files work without any new settings additions.

## Consequences

**Positive:**
- No new infrastructure dependencies. `python-docx` and `openpyxl` were already in
  `requirements.txt`.
- Idempotent re-runs: the same issue or file always maps to the same artifact ID.
- Raw capture provides a fallback for re-normalisation if models change later.
- New source connectors (SharePoint, Confluence, Appian) can be added by implementing
  `BaseIngestionSource` without touching existing code.

**Negative / deferred:**
- `search_artifacts()` does a full list scan — O(n) in the number of JSON files.
  Acceptable for MVP; a manifest or index file will be needed at larger scale.
- SharePoint, Confluence, and Appian connectors are deferred to later phases.
- No chunking or embedding in this phase — `ChunkRecord` objects are not yet populated.
