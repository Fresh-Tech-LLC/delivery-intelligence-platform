"""
ModelEngine.ask adapter.

Wraps a ModelEngine instance whose interface is:

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

Structured output is requested by placing the JSON schema under
param_dict["schema"]. Tool calling is attempted by placing tools and
tool_choice under param_dict — the probe assesses whether the engine
actually honors them.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from backend.app.config import Settings, get_settings
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


def _safe_json(s: Any) -> dict[str, Any]:
    """Parse a JSON string into a dict, returning empty dict on failure."""
    if isinstance(s, dict):
        return s
    try:
        result = json.loads(s)
        return result if isinstance(result, dict) else {}
    except Exception:
        return {}


def _normalize_engine_response(raw: Any) -> AdapterResponse:
    """
    Convert a ModelEngine.ask() return value to AdapterResponse.

    The engine may return:
    - str  : plain text or a JSON string
    - dict : structured response (may contain a "tool_calls" key)
    - other: converted via str()
    """
    if isinstance(raw, dict):
        return AdapterResponse(raw=raw, parsed_json=raw)
    if isinstance(raw, str):
        parsed = None
        try:
            parsed = json.loads(raw)
        except Exception:
            pass
        return AdapterResponse(text=raw, parsed_json=parsed)
    return AdapterResponse(text=str(raw))


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class ModelEngineAskAdapter(LLMAdapter):
    """
    Adapter for a ModelEngine wrapper.

    Structured output: passes param_dict={"schema": schema}
    Tool calling: passes param_dict={"tools": tools, "tool_choice": "auto"}
      — the probe will assess whether the engine actually honors these.
    """

    def __init__(self, engine: Any, settings: Settings | None = None) -> None:
        self._engine = engine   # injected ModelEngine instance (duck-typed)
        self._settings = settings or get_settings()

    def get_name(self) -> str:
        return "model_engine_ask"

    def get_model_name(self) -> str:
        return self._settings.llm_model_name

    def get_feature_support(self) -> AdapterFeatureSupport:
        return AdapterFeatureSupport(
            structured_output=True,   # via param_dict["schema"]
            tool_calling=True,        # attempted via param_dict["tools"]; probe assesses result
            context_input=True,       # via context= param
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
        cmd = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
        param_dict: dict[str, Any] = {}
        if temperature is not None:
            param_dict["temperature"] = temperature
        if max_tokens is not None:
            param_dict["max_tokens"] = max_tokens
        try:
            raw = self._engine.ask(
                command=cmd,
                param_dict=param_dict or None,
                room_id=self._settings.model_engine_default_room_id,
                context=context,
                use_history=self._settings.model_engine_use_history,
                insight_id=self._settings.model_engine_default_insight_id,
            )
        except Exception as exc:
            raise AdapterInvocationError(str(exc)) from exc
        return _normalize_engine_response(raw)

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
        cmd = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
        param_dict: dict[str, Any] = {"schema": schema}
        if temperature is not None:
            param_dict["temperature"] = temperature
        if max_tokens is not None:
            param_dict["max_tokens"] = max_tokens
        try:
            raw = self._engine.ask(
                command=cmd,
                param_dict=param_dict,
                room_id=self._settings.model_engine_default_room_id,
                context=context,
                use_history=self._settings.model_engine_use_history,
                insight_id=self._settings.model_engine_default_insight_id,
            )
        except Exception as exc:
            raise AdapterInvocationError(str(exc)) from exc
        return _normalize_engine_response(raw)

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
        """
        Attempt tool calling by passing tools and tool_choice in param_dict.
        ModelEngine may or may not honor these — the probe assesses the result.
        """
        cmd = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
        param_dict: dict[str, Any] = {
            "tools": tools,
            "tool_choice": "auto",
        }
        if temperature is not None:
            param_dict["temperature"] = temperature
        if max_tokens is not None:
            param_dict["max_tokens"] = max_tokens
        try:
            raw = self._engine.ask(
                command=cmd,
                param_dict=param_dict,
                room_id=self._settings.model_engine_default_room_id,
                context=context,
                use_history=self._settings.model_engine_use_history,
                insight_id=self._settings.model_engine_default_insight_id,
            )
        except Exception as exc:
            raise AdapterInvocationError(str(exc)) from exc

        response = _normalize_engine_response(raw)
        # If the engine returns a dict with "tool_calls", extract and normalize them.
        if isinstance(raw, dict):
            raw_tool_calls = raw.get("tool_calls") or []
            response.tool_calls = [
                ToolCallResult(
                    name=tc.get("function", {}).get("name", ""),
                    arguments=_safe_json(tc.get("function", {}).get("arguments", "{}")),
                )
                for tc in raw_tool_calls
            ]
        return response
