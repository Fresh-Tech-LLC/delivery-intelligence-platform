# Knowledge Phase 4

Phase 4 adds the first Requirements Studio workflow on top of the existing knowledge layer.

## What Phase 4 Adds
- file-backed feature workspaces
- persisted context packs assembled from Phase 2 search and Phase 3 related-artifact logic
- manual evidence pinning by artifact or chunk ref ID
- structured requirements draft generation
- structured backlog draft generation
- a server-rendered Requirements Studio UI and small JSON endpoints

## Storage Layout
- `local_data/requirements_workspaces/workspaces/<workspace_id>.json`
- `local_data/requirements_workspaces/context_packs/<workspace_id>.json`
- `local_data/requirements_workspaces/requirements/<workspace_id>.json`
- `local_data/requirements_workspaces/backlogs/<workspace_id>.json`

## Context Pack Assembly
- query text is built from workspace title plus request text
- lexical chunk search provides the primary evidence set
- related artifacts are expanded from the top artifact hits
- pinned evidence is preserved explicitly
- results are bounded and deduplicated

## Requirements And Backlog Generation
- prompts are loaded from `prompts/requirements_generation.md` and `prompts/backlog_generation.md`
- generation uses the existing OpenAI-compatible LLM client
- outputs must validate against the Phase 4 Pydantic models
- drafts are saved as editable JSON records and can be updated through the UI

## Requirements Studio Routes
- `GET /requirements`
- `GET /requirements/workspaces/{workspace_id}`
- `POST /api/requirements/workspaces`
- `POST /api/requirements/workspaces/{workspace_id}/context-pack`
- `POST /api/requirements/workspaces/{workspace_id}/generate-requirements`
- `POST /api/requirements/workspaces/{workspace_id}/generate-backlog`

## Local Test Flow
1. Start the server with `uvicorn backend.app.main:app --reload`
2. Open `/requirements`
3. Create a workspace
4. Build a context pack
5. Pin any needed artifact or chunk refs manually
6. Generate requirements
7. Generate backlog
8. Edit and save the JSON drafts if needed

## Deferred Work
- Jira publishing
- QA Studio
- Playwright generation
- advanced semantic retrieval
- autonomous orchestration
