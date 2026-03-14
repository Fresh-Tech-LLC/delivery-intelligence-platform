"""
Base classes, data models, and exceptions for the LLM adapter layer.

The adapter layer provides a normalized interface so the capability probe and
main application are decoupled from specific LLM provider implementations.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Feature support flags
# ---------------------------------------------------------------------------


@dataclass
class AdapterFeatureSupport:
    """Declares which capabilities the adapter actually attempts to support."""
    structured_output: bool = False
    tool_calling: bool = False
    images: bool = False
    urls: bool = False
    context_input: bool = False


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class ToolCallResult(BaseModel):
    """A single tool call returned by the model."""
    name: str
    arguments: dict[str, Any] = {}


class AdapterResponse(BaseModel):
    """Normalized response from any adapter method."""
    text: str = ""
    parsed_json: dict[str, Any] | list[Any] | None = None
    tool_calls: list[ToolCallResult] = []
    raw: dict[str, Any] | str | None = None
    latency_ms: float = 0.0


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AdapterUnsupportedFeatureError(Exception):
    """Raised when an operation is not supported by the selected adapter."""


class AdapterInvocationError(Exception):
    """Raised when an adapter call fails due to a provider or network error."""


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class LLMAdapter(ABC):
    """
    Normalized interface for LLM providers.

    Implementors must support ask_text. Structured output and tool calling
    should be attempted if declared in get_feature_support(); if the underlying
    provider does not support them, raise AdapterUnsupportedFeatureError.
    """

    @abstractmethod
    def get_name(self) -> str:
        """Return the adapter type identifier (e.g. 'openai_chat')."""
        ...

    @abstractmethod
    def get_model_name(self) -> str:
        """Return the model name being used."""
        ...

    @abstractmethod
    def get_feature_support(self) -> AdapterFeatureSupport:
        """Return feature support flags for this adapter."""
        ...

    @abstractmethod
    def ask_text(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        context: str | None = None,
    ) -> AdapterResponse:
        """Send a plain-text prompt and return the model's response."""
        ...

    @abstractmethod
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
        """
        Request structured (JSON) output conforming to the given schema.
        parsed_json is populated in the response if the model returns valid JSON.
        """
        ...

    @abstractmethod
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
        Send a prompt with tool definitions. tool_calls in the response will be
        populated if the model invokes any tools.
        """
        ...
