# Project Delivery Copilot — Phase 1 MVP

An agentic workflow assistant for Business Analysts and Project Managers.

- **BA Mode**: Draft requirements → generate story sets → run readiness checks → push to Jira
- **PM Mode**: Query Jira with natural language → get JQL + results

---

## Local Run Steps

### 1. Clone & set up Python environment

```bash
git clone https://github.com/wesley-jones/project-copilot.git
cd project-copilot

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
| `JIRA_BASE_URL` | Your Jira instance URL (optional — dry-run works without it) |
| `JIRA_USER` | Jira account email |
| `JIRA_API_TOKEN` | Jira API token |
| `JIRA_PROJECT_KEY` | Default Jira project key (e.g. `PROJ`) |

> Jira credentials are optional. Without them, Jira dry-run mode will still generate payloads, and the PM mode will generate JQL but not fetch results.

### 4. Run the backend

```bash
# From the repo root
uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

### 5. Open the UI

Open your browser at: **http://localhost:8000**

---

## Project Structure

```
backend/
  app/
    main.py              # FastAPI app + Jinja2 UI routes
    config.py            # Settings (pydantic-settings, reads .env)
    schemas.py           # All Pydantic request/response models
    utils.py             # JSON extraction, auth redaction helpers
    routers/
      ba.py              # BA API endpoints
      pm.py              # PM API endpoint
      jira.py            # Jira create endpoint
    services/
      llm_client.py      # OpenAI-compatible LLM client (retry, JSON mode)
      jira_client.py     # Jira REST API v2 client
      prompt_loader.py   # File-based prompt loader (reads on every request)
      document_store.py  # File-based session workspace storage
      ba_agent.py        # BA workflow orchestration
      pm_agent.py        # PM NL → JQL → results orchestration

prompts/
  ba_requirements.md         # Requirements generation/update prompt
  ba_story_breakdown.md      # Story set JSON generation prompt
  ba_readiness_checklist.md  # Readiness check prompt
  pm_jira_query.md           # NL → JQL prompt

config/
  ba_hidden_checklist.md     # Internal BA quality checklist (not shown to users)

frontend/
  templates/                 # Jinja2 HTML templates
  static/style.css           # UI styles

local_data/                  # Session workspaces (git-ignored)

.env.example                 # Environment variable template
requirements.txt
```

---

## API Endpoints

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

PM mode also supports an optional user-maintained override file (default: `local_data/prompt_overrides/pm_jira_query_user.md`) for instance-specific Jira details like custom fields and team names. See `config/pm_jira_query_user.example.md` for a template.

---

## Security Notes

- Never commit `.env`
- LLM API keys and Jira tokens are never logged
- All LLM calls go through the backend — the browser never calls LLM APIs directly
- Authorization headers are redacted in debug logs
