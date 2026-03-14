"""
Minimal, non-manipulating LLM client for capability probing.

Design rules:
- One attempt only. No retries.
- Returns the raw API response body unmodified.
- Never repairs, re-requests, strips fences, or re-interprets model output.
- Never raises — all errors are captured in ProbeCallResult.
- Caller is fully responsible for assessing what the response means.

This client is used ONLY by the probe runner. The rest of the app
continues to use LLMClient (backend/app/services/llm_client.py).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from backend.app.config import Settings, get_settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------


@dataclass
class ProbeCallResult:
    """
    Raw result from a single probe LLM call.

    content:         choices[0].message.content  — may be None (valid for tool calls)
    tool_calls:      choices[0].message.tool_calls — None if not present
    raw_response:    Full parsed response body, or None on error
    http_status:     HTTP status code received, or None on network/parse error
    error:           Error description string, or None on success
    latency_ms:      End-to-end request duration in milliseconds
    payload_sent:    Sanitized request payload (auth headers stripped)
    """
    content: str | None
    tool_calls: list[dict[str, Any]] | None
    raw_response: dict[str, Any] | None
    http_status: int | None
    error: str | None
    latency_ms: float
    payload_sent: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """True if the request succeeded (2xx) and returned a parseable response."""
        return self.error is None and self.http_status is not None and self.http_status < 300


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class ProbeLLMClient:
    """
    Minimal raw LLM client for probe steps.

    Sends exactly the payload described and returns exactly what the API gives back.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    # ------------------------------------------------------------------
    # Internal helpers (mirrors LLMClient patterns)
    # ------------------------------------------------------------------

    @property
    def _base_url(self) -> str:
        return self._settings.llm_api_base.rstrip("/")

    @property
    def _api_key(self) -> str:
        s = self._settings
        if s.llm_access_key and s.llm_secret_key:
            return f"{s.llm_access_key}:{s.llm_secret_key}"
        return s.llm_api_key

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _build_payload(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float | None,
        max_tokens: int,
        json_mode: bool,
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._settings.llm_model_name,
            "messages": messages,
            self._settings.llm_max_tokens_param: max_tokens,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return payload

    @staticmethod
    def _sanitize_payload(payload: dict[str, Any]) -> dict[str, Any]:
        """Return a copy of the payload safe to store (no auth values)."""
        # The payload is the request body, not headers — it's already auth-free.
        # We do a shallow copy to avoid mutating the original.
        return dict(payload)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float | None = None,
        max_tokens: int = 100,
        json_mode: bool = False,
        tools: list[dict[str, Any]] | None = None,
    ) -> ProbeCallResult:
        """
        Send a single chat completions request and return the raw result.

        Never raises. All failures are captured in ProbeCallResult.error.
        """
        url = f"{self._base_url}/chat/completions"
        payload = self._build_payload(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
            tools=tools,
        )
        payload_sent = self._sanitize_payload(payload)

        t0 = time.monotonic()
        try:
            with httpx.Client(
                timeout=self._settings.probe_step_timeout,
                verify=self._settings.llm_ca_bundle or True,
            ) as client:
                resp = client.post(url, headers=self._headers, json=payload)

            latency_ms = round((time.monotonic() - t0) * 1000, 1)
            http_status = resp.status_code

            # Try to parse the response body as JSON regardless of status code.
            try:
                raw_response = resp.json()
            except Exception as parse_exc:
                raw_response = None
                return ProbeCallResult(
                    content=None,
                    tool_calls=None,
                    raw_response=None,
                    http_status=http_status,
                    error=f"Response body is not valid JSON: {parse_exc} | body: {resp.text[:300]}",
                    latency_ms=latency_ms,
                    payload_sent=payload_sent,
                )

            # Non-2xx: return error with full body for inspection.
            if http_status >= 300:
                error_detail = _extract_error_detail(raw_response, resp.text)
                logger.debug(
                    "ProbeLLMClient: HTTP %d from %s — %s", http_status, url, error_detail[:200]
                )
                return ProbeCallResult(
                    content=None,
                    tool_calls=None,
                    raw_response=raw_response,
                    http_status=http_status,
                    error=error_detail,
                    latency_ms=latency_ms,
                    payload_sent=payload_sent,
                )

            # 2xx: extract message fields.
            try:
                message = raw_response["choices"][0]["message"]
                content = message.get("content")        # str | None
                tool_calls = message.get("tool_calls")  # list | None
            except (KeyError, IndexError, TypeError) as exc:
                return ProbeCallResult(
                    content=None,
                    tool_calls=None,
                    raw_response=raw_response,
                    http_status=http_status,
                    error=f"Unexpected response structure: {exc}",
                    latency_ms=latency_ms,
                    payload_sent=payload_sent,
                )

            return ProbeCallResult(
                content=content,
                tool_calls=tool_calls,
                raw_response=raw_response,
                http_status=http_status,
                error=None,
                latency_ms=latency_ms,
                payload_sent=payload_sent,
            )

        except httpx.TimeoutException as exc:
            latency_ms = round((time.monotonic() - t0) * 1000, 1)
            return ProbeCallResult(
                content=None,
                tool_calls=None,
                raw_response=None,
                http_status=None,
                error=f"Request timed out after {self._settings.probe_step_timeout}s: {exc}",
                latency_ms=latency_ms,
                payload_sent=payload_sent,
            )
        except Exception as exc:
            latency_ms = round((time.monotonic() - t0) * 1000, 1)
            return ProbeCallResult(
                content=None,
                tool_calls=None,
                raw_response=None,
                http_status=None,
                error=f"Unexpected error: {exc}",
                latency_ms=latency_ms,
                payload_sent=payload_sent,
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_error_detail(raw: dict | None, body_text: str) -> str:
    """Pull a readable error string from an API error response."""
    if isinstance(raw, dict):
        # OpenAI-style: {"error": {"message": "..."}}
        err = raw.get("error")
        if isinstance(err, dict):
            return err.get("message") or str(err)
        if isinstance(err, str):
            return err
        # Some gateways: {"detail": "..."}
        detail = raw.get("detail") or raw.get("message")
        if detail:
            return str(detail)
    return body_text[:500] if body_text else "unknown error"
