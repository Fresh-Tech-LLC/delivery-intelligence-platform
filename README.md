# Delivery Intelligence Platform

An agentic platform for Business Analysts and Project Managers that turns raw project data into structured requirements, backlog items, and test artefacts.

- **Knowledge Layer** — ingest, chunk, index, and graph-link project data from Jira, Appian, and local documents
- **Requirements Studio** — AI-assisted requirements drafting and backlog generation grounded in ingested evidence
- **QA Studio** — AI-generated test scenarios, natural language scripts, execution specs, and optional Playwright code
- **BA Mode** — draft requirements from raw notes, generate story sets, and push to Jira
- **PM Mode** — query Jira with natural language, get JQL + results

---

## Local Run Steps

### 1. Clone & set up Python environment

```bash
git clone https://github.com/Fresh-Tech-LLC/delivery-intelligence-platform.git
cd delivery-intelligence-platform

python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and fill in:

| Variable | Description |
|---|---|
| `LLM_API_BASE` | OpenAI-compatible API base URL (e.g. `https://api.openai.com/v1`) |
| `LLM_MODEL_NAME` | Model name (e.g. `gpt-4o`) |
| `LLM_API_KEY` | Your API key |
| `LLM_MAX_TOKENS` | Default completion token limit |
| `JIRA_BASE_URL` | Your Jira instance URL (optional — dry-run works without it) |
| `JIRA_USER` | Jira account email |
| `JIRA_API_TOKEN` | Jira API token |
| `JIRA_PROJECT_KEY` | Default Jira project key (e.g. `PROJ`) |
| `KNOWLEDGE_APPIAN_EXPORTS_DIR` | Path to Appian XML/ZIP export directory (default: `local_data/appian_exports`) |
| `KNOWLEDGE_LOCAL_DOCS_DIR` | Path to local documents directory (default: `local_data/knowledge_docs`) |
| `KNOWLEDGE_DEFAULT_PROJECT_KEY` | Default project key tag applied to ingested artifacts (optional) |
| `KNOWLEDGE_RAW_CAPTURE_ENABLED` | Save raw pre-normalisation payloads for auditing (default: `true`) |
| `REQUIREMENTS_WORKSPACE_DIR` | Storage path for Requirements Studio workspaces (default: `local_data/requirements_workspaces`) |
| `QA_WORKSPACE_DIR` | Storage path for QA Studio workspaces (default: `local_data/qa_workspaces`) |
| `QA_PLAYWRIGHT_ENABLED` | Enable Playwright test code generation (default: `false`) |

> Jira credentials are optional for BA/PM Mode dry runs. They are required for knowledge ingestion from Jira.

### 4. Run the backend

```bash
# From the repo root
uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

### 5. Open the UI

Open your browser at: **http://localhost:8000**

---

## Importing Project Data

Before using the Requirements Studio or QA Studio, import your project data into the knowledge layer.

### From Jira

**Prerequisites:** `JIRA_BASE_URL`, `JIRA_USER`, `JIRA_API_TOKEN`, and `JIRA_PROJECT_KEY` must be set in `.env`.

With the server running, trigger ingestion via the API:

```bash
# Import all issues from the default project
curl -X POST http://localhost:8000/api/knowledge/ingest/jira \
  -H "Content-Type: application/json" \
  -d '{}'

# Import with a custom JQL query
curl -X POST http://localhost:8000/api/knowledge/ingest/jira \
  -H "Content-Type: application/json" \
  -d '{"jql": "project = MYPROJ AND issuetype in (Story, Epic) ORDER BY updated DESC", "max_results": 100}'
```

Re-running is safe — artifact IDs are deterministic and existing records are updated rather than duplicated. Raw API responses are saved to `local_data/knowledge/raw/jira/` for auditing.

### From Appian

1. Copy your Appian export files (`.xml` files or `.zip` archives containing XML) into `local_data/appian_exports/` (or the directory set in `KNOWLEDGE_APPIAN_EXPORTS_DIR`).

2. Trigger ingestion:

```bash
# Ingest all exports from the default directory
curl -X POST http://localhost:8000/api/knowledge/ingest/appian \
  -H "Content-Type: application/json" \
  -d '{}'

# Ingest from a custom path and tag with a project key
curl -X POST http://localhost:8000/api/knowledge/ingest/appian \
  -H "Content-Type: application/json" \
  -d '{"root_dir": "/path/to/exports", "project_key": "MYPROJ", "recursive": true}'
```

ZIP archives are extracted recursively. The maximum file size is 50 MB per file. Object types (interfaces, process models, integrations, data types, etc.) are inferred from the XML structure.

### From local documents

Drop `.txt`, `.md`, `.docx`, or `.xlsx` files into `local_data/knowledge_docs/` and run:

```bash
curl -X POST http://localhost:8000/api/knowledge/ingest/local-docs \
  -H "Content-Type: application/json" \
  -d '{}'
```

### Verify ingestion

After ingesting, confirm your data is indexed:

```bash
curl "http://localhost:8000/api/knowledge/search?q=your+search+term"
```

---

## Using the Studios

### Requirements Studio

1. Browse to **http://localhost:8000/requirements**
2. Create a new workspace and describe the feature or change under investigation
3. Use the **Evidence** and **Context** panels to search the knowledge layer and pin relevant artifacts
4. Click **Generate Requirements** to produce an AI-drafted requirements document grounded in your pinned evidence
5. Review, edit, and validate the draft in the workspace
6. Generate a structured backlog (epics, features, stories, tasks) from the approved requirements
7. Export requirements and backlog as JSON from the **Export** panel

### QA Studio

1. Browse to **http://localhost:8000/qa**
2. Create a QA workspace linked to an existing Requirements workspace
3. Generate test scenarios, natural language scripts, and structured execution specs
4. Optionally generate Playwright test code (requires `QA_PLAYWRIGHT_ENABLED=true` in `.env`)
5. Use the **Traceability** view to verify requirements-to-test coverage

---

## Project Structure

```
backend/
  app/
    main.py              # FastAPI app + Jinja2 UI routes
    config.py            # Settings (pydantic-settings, reads .env)
    routers/
      knowledge.py       # Knowledge ingestion + search endpoints
      requirements.py    # Requirements Studio endpoints
      qa.py              # QA Studio endpoints
      ba.py              # BA Mode endpoints
      pm.py              # PM Mode endpoint
      jira.py            # Jira create endpoint
    services/
      knowledge_service.py    # Knowledge layer facade
      ingestion/              # Jira, Appian, local docs ingestors + raw store
      parsers/                # Text, DOCX, XLSX, XML parsers
      chunking/               # Deterministic text chunking
      indexing/               # Lexical inverted index + retrieval
      graph/                  # Graph edge linking + entity extraction
      pipelines/              # Ingest, chunk, link orchestration pipelines
      requirements/           # Requirements Studio services + LLM generators
      qa/                     # QA Studio services + LLM generators
      llm_client.py           # OpenAI-compatible LLM client (retry, JSON mode)
      jira_client.py          # Jira REST API v2 client

prompts/                 # File-based prompts (hot-reloaded, no restart needed)
config/                  # Hidden checklists and PM override templates

frontend/
  templates/
    requirements_index.html      # Requirements Studio workspace list
    requirements_workspace.html  # Multi-panel requirements dashboard
    partials/                    # Dashboard panel partials (investigation,
                                 # context, evidence, draft, review, backlog,
                                 # validation, history, export)

local_data/              # All runtime data (git-ignored)
  appian_exports/        # Drop Appian XML/ZIP exports here
  knowledge_docs/        # Drop local documents here for ingestion
  knowledge/             # Indexed artifacts, chunks, edges, search indexes, raw captures
  requirements_workspaces/
  qa_workspaces/
```

---

## API Endpoints

### Knowledge / Ingestion
| Method | Path | Description |
|---|---|---|
| POST | `/api/knowledge/ingest/jira` | Pull issues from Jira and index them |
| POST | `/api/knowledge/ingest/appian` | Parse Appian XML/ZIP exports and index them |
| POST | `/api/knowledge/ingest/local-docs` | Scan local docs directory and index documents |
| GET | `/api/knowledge/artifacts` | List all indexed artifacts |
| GET | `/api/knowledge/artifacts/search` | Filter artifacts by source system, kind, or project |
| GET | `/api/knowledge/search` | Full-text lexical search over all indexed content |
| GET | `/api/knowledge/related/{artifact_id}` | Get graph-linked related artifacts |
| GET | `/api/knowledge/runs` | List ingestion run history and stats |
| POST | `/api/knowledge/chunk-all` | Chunk all artifacts for search indexing |
| POST | `/api/knowledge/index/rebuild` | Rebuild the lexical search index |
| POST | `/api/knowledge/link-all` | Rebuild graph edges between all artifacts |

### Requirements Studio
| Method | Path | Description |
|---|---|---|
| GET | `/requirements` | List all workspaces (UI) |
| POST | `/requirements/workspaces` | Create a new workspace |
| GET | `/requirements/workspaces/{id}` | Open workspace dashboard (UI) |
| POST | `/requirements/workspaces/{id}/context-pack` | Assemble evidence from knowledge layer |
| POST | `/requirements/workspaces/{id}/generate-requirements` | AI-generate requirements draft |
| POST | `/requirements/workspaces/{id}/generate-backlog` | AI-generate backlog (epics/stories/tasks) |
| POST | `/requirements/workspaces/{id}/review` | Mark requirements as reviewed |
| POST | `/requirements/workspaces/{id}/validate` | Run validation checks |
| GET | `/requirements/workspaces/{id}/export/requirements.json` | Download requirements JSON |
| GET | `/requirements/workspaces/{id}/export/backlog.json` | Download backlog JSON |

### QA Studio
| Method | Path | Description |
|---|---|---|
| GET | `/qa` | List all QA workspaces (UI) |
| POST | `/qa/workspaces` | Create QA workspace from a Requirements workspace |
| GET | `/qa/workspaces/{id}` | Open QA workspace dashboard (UI) |
| POST | `/qa/workspaces/{id}/generate-scenarios` | AI-generate test scenarios |
| POST | `/qa/workspaces/{id}/generate-nl-scripts` | AI-generate natural language test scripts |
| POST | `/qa/workspaces/{id}/generate-execution-specs` | Generate structured execution specs |
| POST | `/qa/workspaces/{id}/generate-playwright` | Generate Playwright test code (requires `QA_PLAYWRIGHT_ENABLED=true`) |
| POST | `/qa/workspaces/{id}/traceability` | Map requirements to test scenarios |

### BA Mode
| Method | Path | Description |
|---|---|---|
| POST | `/api/ba/requirements/generate` | Generate requirements from raw notes |
| POST | `/api/ba/requirements/update` | Apply NL edit to requirements |
| POST | `/api/ba/stories/generate` | Generate story set JSON |
| POST | `/api/ba/stories/update` | Apply NL edit to story set |
| POST | `/api/ba/readiness/check` | Run readiness check |
| POST | `/api/ba/docs/upload` | Upload supporting doc (.txt/.md) |

### Jira
| Method | Path | Description |
|---|---|---|
| POST | `/api/jira/create_story_set` | Create epic+stories in Jira (`dry_run: true` by default) |

### PM Mode
| Method | Path | Description |
|---|---|---|
| POST | `/api/pm/jira/query` | NL → JQL → Jira results |

Full interactive API docs available at **http://localhost:8000/docs**

---

## Customising Prompts

All prompts live in `/prompts/` and are loaded on every request — edit them without restarting the server.

The hidden BA checklist lives in `/config/ba_hidden_checklist.md`. It is injected as internal guidance for the LLM but never exposed to users.

PM mode supports an optional user-maintained override file (default: `local_data/prompt_overrides/pm_jira_query_user.md`) for instance-specific Jira details like custom fields and team names. See `config/pm_jira_query_user.example.md` for a template.

---

## Security Notes

- Never commit `.env`
- LLM API keys and Jira tokens are never logged
- All LLM calls go through the backend — the browser never calls LLM APIs directly
- Authorization headers are redacted in debug logs
