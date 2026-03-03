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
        json_mode: bool = False,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._settings.llm_model_name,
            "messages": messages,
            self._settings.llm_max_tokens_param: max_tokens,
        }
        if self._settings.llm_temperature_supported:
            payload["temperature"] = temperature
        if self._settings.llm_seed is not None:
            payload["seed"] = self._settings.llm_seed
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        json_mode: bool = False,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> str:
        """
        Send a chat completion request. Returns the assistant message content.
        Retries up to llm_max_retries times on transient errors.
        """
        url = f"{self._base_url}/chat/completions"
        payload = self._build_payload(
            messages, json_mode=json_mode, temperature=temperature, max_tokens=max_tokens
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
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> Any:
        """
        Chat completion that returns parsed JSON.
        On parse failure, sends one auto-repair request.
        """
        raw = self.chat(messages, json_mode=True, temperature=temperature, max_tokens=max_tokens)
        logger.debug("LLM raw json_mode output: %s", self._preview(raw))
        try:
            return extract_json(raw)
        except ValueError as first_err:
            logger.warning("JSON parse failed on first attempt: %s - attempting repair", first_err)
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
                repair_messages, json_mode=True, temperature=0.0, max_tokens=max_tokens
            )
            logger.debug("LLM raw json_mode repair output: %s", self._preview(raw2))
            try:
                return extract_json(raw2)
            except ValueError as second_err:
                raise LLMError(
                    f"LLM produced invalid JSON after repair attempt: {second_err}"
                ) from second_err

    def _preview(self, text: str, limit: int = 800) -> str:
        """Return a single-line, truncated preview safe for debug logs."""
        collapsed = " ".join((text or "").split())
        if len(collapsed) > limit:
            return collapsed[:limit] + " ...(truncated)"
        return collapsed


def get_llm_client() -> LLMClient:
    return LLMClient()
