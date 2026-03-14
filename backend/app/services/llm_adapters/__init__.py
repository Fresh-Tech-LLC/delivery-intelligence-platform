"""
LLM Adapter Layer — public API.

Supported adapter types (hardcoded POC):
  - "openai_chat"       OpenAI-compatible endpoint via ProbeLLMClient
  - "model_engine_ask"  ModelEngine.ask() wrapper

Adapter selection is driven by settings.llm_adapter_type (.env: LLM_ADAPTER_TYPE).
No auto-detection. No plugin system. No fallback chain.

Usage:
    from backend.app.services.llm_adapters import get_llm_adapter, AdapterResponse

    adapter = get_llm_adapter()
    response = adapter.ask_text("Summarise this document.", context=document_text)
"""
from __future__ import annotations

from functools import lru_cache

from backend.app.config import get_settings
from backend.app.services.llm_adapters.base import (
    AdapterFeatureSupport,
    AdapterInvocationError,
    AdapterResponse,
    AdapterUnsupportedFeatureError,
    LLMAdapter,
    ToolCallResult,
)
from backend.app.services.llm_adapters.model_engine_adapter import ModelEngineAskAdapter
from backend.app.services.llm_adapters.openai_adapter import OpenAIChatAdapter

__all__ = [
    "LLMAdapter",
    "AdapterResponse",
    "AdapterFeatureSupport",
    "ToolCallResult",
    "AdapterInvocationError",
    "AdapterUnsupportedFeatureError",
    "OpenAIChatAdapter",
    "ModelEngineAskAdapter",
    "get_llm_adapter",
]


@lru_cache(maxsize=1)
def get_llm_adapter() -> LLMAdapter:
    """
    Return the configured LLM adapter (cached singleton).

    Raises:
        ValueError  — unknown llm_adapter_type value
        ImportError — model_engine_ask selected but model_engine module not implemented
    """
    settings = get_settings()
    if settings.llm_adapter_type == "openai_chat":
        return OpenAIChatAdapter(settings)
    if settings.llm_adapter_type == "model_engine_ask":
        return ModelEngineAskAdapter(settings)  # loads engine from MODEL_ENGINE_CLASS in .env
    raise ValueError(
        f"Unknown llm_adapter_type: {settings.llm_adapter_type!r}. "
        "Supported values: 'openai_chat', 'model_engine_ask'"
    )
