"""
OpenAI-compatible LLM adapter.

Wraps ProbeLLMClient for honest, no-retry, no-repair behavior.
The main application's existing LLMClient usage is unchanged.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from backend.app.config import Settings, get_settings
from backend.app.services.capability_probe.probe_llm_client import (
    ProbeLLMClient,
    ProbeCallResult,
)
from backend.app.services.llm_adapters.base import (
    AdapterFeatureSupport,
    AdapterInvocationError,
    AdapterResponse,
    LLMAdapter,
    ToolCallResult,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_messages(
    prompt: str,
    *,
    system_prompt: str | None,
    context: str | None,
) -> list[dict[str, str]]:
    """Build an OpenAI-compatible messages list."""
    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    user_content = f"Context:\n{context}\n\n{prompt}" if context else prompt
    messages.append({"role": "user", "content": user_content})
    return messages


def _raise_if_error(result: ProbeCallResult) -> None:
    """Raise AdapterInvocationError if the probe result indicates a failure."""
    if result.http_status is not None and result.http_status >= 400:
        raise AdapterInvocationError(
            f"HTTP {result.http_status}: {result.error or 'unknown error'}"
        )
    if result.error:
        raise AdapterInvocationError(result.error)


def _safe_json(s: Any) -> dict[str, Any]:
    """Parse a JSON string into a dict, returning empty dict on failure."""
    if isinstance(s, dict):
        return s
    try:
        result = json.loads(s)
        return result if isinstance(result, dict) else {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class OpenAIChatAdapter(LLMAdapter):
    """
    Adapter for OpenAI-compatible endpoints.

    Uses ProbeLLMClient to ensure honest probe behavior:
    - single attempt (no retries)
    - raw response returned (no JSON repair, no fence stripping)
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = ProbeLLMClient(self._settings)

    def get_name(self) -> str:
        return "openai_chat"

    def get_model_name(self) -> str:
        return self._settings.llm_model_name

    def get_feature_support(self) -> AdapterFeatureSupport:
        return AdapterFeatureSupport(
            structured_output=True,
            tool_calling=True,
            context_input=True,
        )

    def ask_text(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        context: str | None = None,
    ) -> AdapterResponse:
        messages = _build_messages(prompt, system_prompt=system_prompt, context=context)
        result = self._client.send(
            messages,
            temperature=temperature,
            max_tokens=max_tokens or 500,
        )
        _raise_if_error(result)
        return AdapterResponse(
            text=result.content or "",
            raw=result.raw_response,
            latency_ms=result.latency_ms,
        )

    def ask_structured(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        context: str | None = None,
    ) -> AdapterResponse:
        messages = _build_messages(prompt, system_prompt=system_prompt, context=context)
        result = self._client.send(
            messages,
            temperature=temperature,
            max_tokens=max_tokens or 500,
            json_mode=True,
        )
        _raise_if_error(result)
        # Attempt to parse; probe runner assesses the result honestly
        parsed = None
        try:
            parsed = json.loads(result.content or "")
        except Exception:
            pass
        return AdapterResponse(
            text=result.content or "",
            parsed_json=parsed,
            raw=result.raw_response,
            latency_ms=result.latency_ms,
        )

    def ask_with_tools(
        self,
        prompt: str,
        tools: list[dict[str, Any]],
        *,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        context: str | None = None,
    ) -> AdapterResponse:
        messages = _build_messages(prompt, system_prompt=system_prompt, context=context)
        result = self._client.send(
            messages,
            temperature=temperature,
            max_tokens=max_tokens or 200,
            tools=tools,
        )
        _raise_if_error(result)
        tool_calls = [
            ToolCallResult(
                name=tc.get("function", {}).get("name", ""),
                arguments=_safe_json(tc.get("function", {}).get("arguments", "{}")),
            )
            for tc in (result.tool_calls or [])
        ]
        return AdapterResponse(
            text=result.content or "",
            tool_calls=tool_calls,
            raw=result.raw_response,
            latency_ms=result.latency_ms,
        )
