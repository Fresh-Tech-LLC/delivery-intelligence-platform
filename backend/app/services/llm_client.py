"""
OpenAI-compatible LLM client.

Rules:
- Never log API keys or Authorization header values.
- Support chat completions with optional JSON mode.
- Retry on transient errors up to settings.llm_max_retries times.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

import httpx

from backend.app.config import get_settings
from backend.app.utils import extract_json, redact_auth

logger = logging.getLogger(__name__)


class LLMError(Exception):
    pass


class LLMClient:
    def __init__(self) -> None:
        self._settings = get_settings()

    @property
    def default_max_tokens(self) -> int:
        return self._settings.llm_max_tokens

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
        messages: list[dict[str, str]],
        *,
        model_name: Optional[str] = None,
        json_mode: bool = False,
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        tools: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        resolved_max_tokens = self._settings.llm_max_tokens if max_tokens is None else max_tokens
        payload: dict[str, Any] = {
            "model": model_name or self._settings.llm_model_name,
            "messages": messages,
            self._settings.llm_max_tokens_param: resolved_max_tokens,
        }
        if self._settings.llm_temperature_supported:
            payload["temperature"] = temperature
        if self._settings.llm_seed is not None:
            payload["seed"] = self._settings.llm_seed
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return payload

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model_name: Optional[str] = None,
        json_mode: bool = False,
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
    ) -> str:
        """
        Send a chat completion request. Returns the assistant message content.
        Retries up to llm_max_retries times on transient errors.
        """
        url = f"{self._base_url}/chat/completions"
        payload = self._build_payload(
            messages,
            model_name=model_name,
            json_mode=json_mode,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        logger.debug(
            "LLM request url=%s headers=%s",
            url,
            redact_auth(self._headers),
        )

        last_exc: Optional[Exception] = None
        for attempt in range(self._settings.llm_max_retries + 1):
            try:
                with httpx.Client(
                    timeout=self._settings.llm_timeout,
                    verify=self._settings.llm_ca_bundle or True,
                ) as client:
                    resp = client.post(url, headers=self._headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                content: str = data["choices"][0]["message"]["content"]
                logger.debug("LLM response received (attempt %d)", attempt + 1)
                return content
            except httpx.TimeoutException as exc:
                last_exc = exc
                logger.warning("LLM timeout on attempt %d: %s", attempt + 1, exc)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                body = exc.response.text
                logger.error("LLM HTTP %d response body: %s", status, body)
                # Retry transient statuses, surface others.
                if status in (429, 502, 503, 504):
                    last_exc = exc
                    wait = 2 ** attempt
                    logger.warning(
                        "LLM HTTP %d on attempt %d; retrying in %ds", status, attempt + 1, wait
                    )
                    time.sleep(wait)
                else:
                    raise LLMError(f"LLM HTTP error {status}: {body}") from exc
            except Exception as exc:
                raise LLMError(f"LLM unexpected error: {exc}") from exc

        raise LLMError(f"LLM failed after {self._settings.llm_max_retries + 1} attempts: {last_exc}")

    def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        model_name: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: Optional[int] = None,
    ) -> Any:
        """
        Chat completion that returns parsed JSON.
        On parse failure, sends one auto-repair request.
        """
        raw = self.chat(
            messages,
            model_name=model_name,
            json_mode=True,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        logger.debug("LLM raw json_mode output: %s", self._preview(raw))
        try:
            return extract_json(raw)
        except ValueError as first_err:
            logger.warning("JSON parse failed on first attempt: %s - attempting repair", first_err)
            logger.warning("Raw LLM output (first attempt):\n%s", raw)
            repair_messages = messages + [
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": (
                        f"Your previous response could not be parsed as JSON. "
                        f"Error: {first_err}. "
                        "Please output ONLY valid JSON - no markdown, no prose, no fences."
                    ),
                },
            ]
            raw2 = self.chat(
                repair_messages,
                model_name=model_name,
                json_mode=True,
                temperature=0.0,
                max_tokens=max_tokens,
            )
            logger.debug("LLM raw json_mode repair output: %s", self._preview(raw2))
            try:
                return extract_json(raw2)
            except ValueError as second_err:
                logger.warning("Raw LLM output (repair attempt):\n%s", raw2)
                raise LLMError(
                    f"LLM produced invalid JSON after repair attempt: {second_err}"
                ) from second_err

    def chat_with_tools(
        self,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]],
        *,
        model_name: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        Send a chat completion request with tool definitions.

        Returns the full assistant message dict (choices[0]["message"]), which may contain:
          - "content": str   — if the model replied in plain text
          - "tool_calls": list[dict] — if the model invoked a tool

        Retries on transient errors identically to chat().
        """
        url = f"{self._base_url}/chat/completions"
        payload = self._build_payload(
            messages,
            model_name=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
        )
        logger.debug(
            "LLM tool-call request url=%s headers=%s",
            url,
            redact_auth(self._headers),
        )

        last_exc: Optional[Exception] = None
        for attempt in range(self._settings.llm_max_retries + 1):
            try:
                with httpx.Client(
                    timeout=self._settings.llm_timeout,
                    verify=self._settings.llm_ca_bundle or True,
                ) as client:
                    resp = client.post(url, headers=self._headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                message: dict[str, Any] = data["choices"][0]["message"]
                logger.debug("LLM tool-call response received (attempt %d)", attempt + 1)
                return message
            except httpx.TimeoutException as exc:
                last_exc = exc
                logger.warning("LLM tool-call timeout on attempt %d: %s", attempt + 1, exc)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                body = exc.response.text
                logger.error("LLM tool-call HTTP %d response body: %s", status, body)
                if status in (429, 502, 503, 504):
                    last_exc = exc
                    wait = 2 ** attempt
                    logger.warning(
                        "LLM tool-call HTTP %d on attempt %d; retrying in %ds",
                        status, attempt + 1, wait,
                    )
                    time.sleep(wait)
                else:
                    raise LLMError(f"LLM tool-call HTTP error {status}: {body}") from exc
            except Exception as exc:
                raise LLMError(f"LLM tool-call unexpected error: {exc}") from exc

        raise LLMError(
            f"LLM tool-call failed after {self._settings.llm_max_retries + 1} attempts: {last_exc}"
        )

    def _preview(self, text: str, limit: int = 800) -> str:
        """Return a single-line, truncated preview safe for debug logs."""
        collapsed = " ".join((text or "").split())
        if len(collapsed) > limit:
            return collapsed[:limit] + " ...(truncated)"
        return collapsed


def get_llm_client() -> LLMClient:
    return LLMClient()
