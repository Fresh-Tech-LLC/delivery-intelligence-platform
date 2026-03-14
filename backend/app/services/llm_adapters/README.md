# LLM Adapter Layer

A small, hardcoded adapter layer that normalizes the interface between the
capability probe (and optionally the main app) and LLM provider implementations.

## Design

- **No plugin system.** Adapters are hardcoded.
- **No auto-detection.** Selection comes from config only.
- **No fallback chain.** Invalid config fails with a clear error.
- **Two adapters only** (POC scope).

## Supported Adapter Types

### `openai_chat` (default)

Wraps `ProbeLLMClient` for honest, single-attempt, no-repair behavior.

```env
LLM_ADAPTER_TYPE=openai_chat
LLM_API_BASE=https://api.openai.com/v1
LLM_MODEL_NAME=gpt-4.1
LLM_API_KEY=sk-...
```

Feature support: `structured_output=True`, `tool_calling=True`, `context_input=True`

### `model_engine_ask`

Wraps a `ModelEngine` instance whose interface is:

```python
ModelEngine.ask(
    command: str,
    param_dict: dict | None = None,
    room_id: str | None = None,
    context: str | None = None,
    image: list | None = None,
    url: list | None = None,
    use_history: bool | None = None,
    insight_id: str | None = None,
)
```

```env
LLM_ADAPTER_TYPE=model_engine_ask
LLM_MODEL_NAME=gemini-2.5-pro
MODEL_ENGINE_DEFAULT_ROOM_ID=     # optional
MODEL_ENGINE_USE_HISTORY=false
MODEL_ENGINE_DEFAULT_INSIGHT_ID=  # optional
```

Feature support: `structured_output=True`, `tool_calling=True` (attempted), `context_input=True`

**Structured output** is requested by placing the JSON schema in `param_dict["schema"]`.

**Tool calling** is attempted by passing `param_dict={"tools": [...], "tool_choice": "auto"}`.
The capability probe will assess the actual response and report pass/warning/fail.

## Adapter Selection

```python
from backend.app.services.llm_adapters import get_llm_adapter

adapter = get_llm_adapter()          # returns cached singleton
response = adapter.ask_text("...")
```

## Adding the ModelEngine Implementation

The factory imports `backend.app.services.model_engine.get_model_engine` when
`llm_adapter_type = "model_engine_ask"`. Create that module and function to wire in
the real engine instance.

## Main App Migration

Existing `LLMClient` usage in service modules is **unchanged**. The adapter layer is
additive — new code can use `get_llm_adapter()` directly.

TODO: Migrate remaining service modules (ba_agent, pm_agent, power_agent, etc.)
to use the adapter layer once the POC is validated.
