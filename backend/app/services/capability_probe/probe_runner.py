"""
Capability probe step execution.

ProbeRunner.run() is a blocking call designed to be called from a daemon thread.
It executes all probe steps sequentially, writing incremental progress to disk
after every step so the polling endpoint always reflects the latest state.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

from backend.app.config import Settings, get_settings
from backend.app.models.capability_probe import (
    CapabilityAssessment,
    CapabilityProbeReport,
    CapabilityProbeRun,
    CapabilityProbeStepResult,
    FinalRecommendation,
    ProbeStatus,
    StepStatus,
)
from backend.app.services.capability_probe.probe_llm_client import ProbeLLMClient
from backend.app.services.capability_probe.probe_store import ProbeStore

logger = logging.getLogger(__name__)

# Ordered list of step names — total_steps is derived from this at run creation.
PROBE_STEPS = [
    "connectivity_check",
    "simple_generation",
    "deterministic_generation",
    "structured_json_output",
    "long_context_smoke",
    "tool_call_readiness",
]

# Tool definition used by the tool_call_readiness step.
_GET_WEATHER_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get the current weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "The city name, e.g. London",
                },
            },
            "required": ["location"],
        },
    },
}

_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _elapsed_ms(started_at: str | None) -> float | None:
    if started_at is None:
        return None
    try:
        start = datetime.fromisoformat(started_at)
        return round((datetime.now(timezone.utc) - start).total_seconds() * 1000, 1)
    except Exception:
        return None


def _extract_fence(text: str) -> str | None:
    """Return the first fenced code block content, or None if none found."""
    m = _FENCE_RE.search(text)
    return m.group(1).strip() if m else None


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------


def grade_report(
    run_id: str,
    steps: list[CapabilityProbeStepResult],
    probe_meta: dict[str, Any],
) -> CapabilityProbeReport:
    """
    Derive a final recommendation from step assessments.

    Rules (explicit if/elif — no magic):
    - not_suitable : connectivity, simple_generation, or structured_json_output failed
    - suitable     : simple_generation + structured_json_output both pass,
                     long_context_smoke is pass/warning/unknown,
                     no critical failures
    - usable       : everything else (generation works but something is degraded)

    tool_call_readiness is informational: unknown does NOT block "suitable".
    deterministic_generation warning does NOT block "suitable".
    """
    a = {s.name: s.assessment for s in steps}
    F = CapabilityAssessment.fail
    P = CapabilityAssessment.pass_
    W = CapabilityAssessment.warning
    U = CapabilityAssessment.unknown

    conn = a.get("connectivity_check", U)
    gen = a.get("simple_generation", U)
    json_out = a.get("structured_json_output", U)
    ctx = a.get("long_context_smoke", U)

    if conn == F or gen == F or json_out == F:
        rec = FinalRecommendation.not_suitable
        summary = "Critical capability failures prevent reliable use of this integration."
    elif gen == P and json_out == P and ctx in (P, W, U):
        rec = FinalRecommendation.suitable
        summary = "Integration supports DIP's basic agentic workflow needs."
    else:
        rec = FinalRecommendation.usable
        summary = (
            "Integration is functional for basic tasks but has limitations "
            "for full agentic workflows."
        )

    return CapabilityProbeReport(
        run_id=run_id,
        generated_at=_now(),
        recommendation=rec,
        summary=summary,
        per_capability={s.name: s.assessment.value for s in steps},
        timings={s.name: s.duration_ms for s in steps},
        raw_results={s.name: s.details for s in steps},
        probe_meta=probe_meta,
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class ProbeRunner:
    def __init__(
        self,
        store: ProbeStore,
        llm_client: Any = None,  # kept for backwards compat — probe uses ProbeLLMClient
        settings: Settings | None = None,
    ) -> None:
        self._store = store
        self._settings = settings or get_settings()
        self._probe_client = ProbeLLMClient(self._settings)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self, run_id: str) -> None:
        """
        Blocking execution. Called from a daemon thread via ProbeService.
        Writes incremental progress after every step.
        """
        run = self._store.get_run(run_id)
        if run is None:
            logger.error("ProbeRunner: run %s not found in store", run_id)
            return

        run.status = ProbeStatus.running
        self._store.save_run(run)

        steps = [CapabilityProbeStepResult(name=n) for n in PROBE_STEPS]
        self._store.save_steps(run_id, steps)

        try:
            for i, step in enumerate(steps):
                run.current_step = step.name
                run.steps = steps
                self._store.save_run(run)

                step.status = StepStatus.running
                step.started_at = _now()
                self._store.save_steps(run_id, steps)

                try:
                    self._dispatch(step)
                except Exception as exc:
                    # Per-step guard: unexpected errors become a failed step.
                    logger.exception("Unexpected error in step %s: %s", step.name, exc)
                    step.status = StepStatus.failed
                    step.assessment = CapabilityAssessment.fail
                    step.summary = str(exc)[:500]

                step.finished_at = _now()
                step.duration_ms = _elapsed_ms(step.started_at)
                run.completed_steps = i + 1
                self._store.save_steps(run_id, steps)
                self._store.save_run(run)

            report = grade_report(run_id, steps, run.probe_meta)
            self._store.save_report(report)
            run.status = ProbeStatus.completed

        except Exception as exc:
            # Top-level guard — partial steps already persisted.
            logger.exception("ProbeRunner top-level failure for run %s: %s", run_id, exc)
            run.status = ProbeStatus.failed
            run.error = str(exc)[:500]

        finally:
            run.finished_at = _now()
            run.current_step = None
            run.steps = steps
            self._store.save_run(run)

    # ------------------------------------------------------------------
    # Step dispatch
    # ------------------------------------------------------------------

    def _dispatch(self, step: CapabilityProbeStepResult) -> None:
        dispatch = {
            "connectivity_check": self._step_connectivity,
            "simple_generation": self._step_simple_generation,
            "deterministic_generation": self._step_deterministic_generation,
            "structured_json_output": self._step_structured_json,
            "long_context_smoke": self._step_long_context_smoke,
            "tool_call_readiness": self._step_tool_call_readiness,
        }
        fn = dispatch.get(step.name)
        if fn is None:
            step.status = StepStatus.skipped
            step.assessment = CapabilityAssessment.unknown
            step.summary = f"Unknown step: {step.name}"
        else:
            fn(step)

    # ------------------------------------------------------------------
    # Individual steps
    # ------------------------------------------------------------------

    def _step_connectivity(self, step: CapabilityProbeStepResult) -> None:
        """Simple connectivity check — minimal prompt, expect any non-empty reply."""
        messages = [{"role": "user", "content": "Reply with the single word OK."}]
        result = self._probe_client.send(messages, temperature=0.0, max_tokens=5)

        if result.http_status is not None and result.http_status >= 400:
            step.status = StepStatus.failed
            step.assessment = CapabilityAssessment.fail
            step.summary = f"API returned HTTP {result.http_status}: {(result.error or '')[:300]}"
        elif result.error:
            step.status = StepStatus.failed
            step.assessment = CapabilityAssessment.fail
            step.summary = f"API unreachable: {result.error[:300]}"
        elif result.content and result.content.strip():
            step.status = StepStatus.passed
            step.assessment = CapabilityAssessment.pass_
            step.summary = "API reachable and returned a response."
        else:
            step.status = StepStatus.failed
            step.assessment = CapabilityAssessment.fail
            step.summary = "API returned null or empty content."

        step.details = {
            "latency_ms": result.latency_ms,
            "http_status": result.http_status,
            "response_preview": (result.content or "")[:200],
            "error": result.error,
        }

    def _step_simple_generation(self, step: CapabilityProbeStepResult) -> None:
        """Basic generation quality and latency check."""
        messages = [
            {"role": "user", "content": "Describe the role of a product owner in one sentence."}
        ]
        result = self._probe_client.send(messages, temperature=0.7, max_tokens=100)

        if result.http_status is not None and result.http_status >= 400:
            step.status = StepStatus.failed
            step.assessment = CapabilityAssessment.fail
            step.summary = f"Generation request failed (HTTP {result.http_status}): {(result.error or '')[:300]}"
        elif result.error:
            step.status = StepStatus.failed
            step.assessment = CapabilityAssessment.fail
            step.summary = f"Generation failed: {result.error[:300]}"
        else:
            length = len(result.content.strip()) if result.content else 0
            if length > 20:
                step.status = StepStatus.passed
                step.assessment = CapabilityAssessment.pass_
                step.summary = f"Generated a {length}-char response."
            elif length > 0:
                step.status = StepStatus.warning
                step.assessment = CapabilityAssessment.warning
                step.summary = f"Response was very short ({length} chars)."
            else:
                step.status = StepStatus.failed
                step.assessment = CapabilityAssessment.fail
                step.summary = "Model returned null or empty content."

        step.details = {
            "latency_ms": result.latency_ms,
            "http_status": result.http_status,
            "response_length": len((result.content or "").strip()),
            "response_preview": (result.content or "")[:300],
            "error": result.error,
        }

    def _step_deterministic_generation(self, step: CapabilityProbeStepResult) -> None:
        """Run the same low-temperature prompt twice and compare outputs."""
        prompt = (
            'List three primary colors. Reply with only a JSON array of strings, '
            'e.g. ["red", "green", "blue"].'
        )
        messages = [{"role": "user", "content": prompt}]

        def _normalize(s: str) -> str:
            return " ".join(s.lower().split())

        result1 = self._probe_client.send(messages, temperature=0.0, max_tokens=150)
        result2 = self._probe_client.send(messages, temperature=0.0, max_tokens=150)

        # Treat any error or empty result as failure.
        if result1.error or result2.error or not result1.content or not result2.content:
            step.status = StepStatus.failed
            step.assessment = CapabilityAssessment.fail
            step.summary = "One or both runs failed or returned empty output."
            step.details = {
                "run1_error": result1.error,
                "run2_error": result2.error,
                "run1_http_status": result1.http_status,
                "run2_http_status": result2.http_status,
            }
            return

        n1, n2 = _normalize(result1.content), _normalize(result2.content)
        identical = n1 == n2

        if identical:
            step.status = StepStatus.passed
            step.assessment = CapabilityAssessment.pass_
            step.summary = "Both low-temperature runs produced identical normalized output."
        else:
            step.status = StepStatus.warning
            step.assessment = CapabilityAssessment.warning
            step.summary = "Low-temperature runs produced different outputs (non-deterministic)."

        step.details = {
            "identical": identical,
            "run1_preview": result1.content[:200],
            "run2_preview": result2.content[:200],
        }

    def _step_structured_json(self, step: CapabilityProbeStepResult) -> None:
        """
        Verify the model can emit valid JSON when json_mode is requested.

        Assessment:
        - pass    : response parses as valid JSON directly (no fences, no repair)
        - warning : response wrapped in markdown fences but contains valid JSON inside
        - fail    : HTTP error, null/empty content, or content is not valid JSON at all
        """
        prompt = (
            "Return valid JSON only — no prose, no markdown fences.\n"
            'Schema: {"name": "<non-empty string>", "items": [{"id": <integer>, "label": "<string>"}, ...]}\n'
            "Include at least 2 items. Use any realistic values."
        )
        messages = [{"role": "user", "content": prompt}]
        result = self._probe_client.send(
            messages, temperature=0.1, max_tokens=300, json_mode=True
        )

        if result.http_status is not None and result.http_status >= 400:
            step.status = StepStatus.failed
            step.assessment = CapabilityAssessment.fail
            step.summary = f"json_mode request rejected (HTTP {result.http_status}): {(result.error or '')[:300]}"
            step.details = {
                "http_status": result.http_status,
                "error": result.error,
                "raw_content_preview": None,
                "json_mode_clean": False,
                "schema_valid": False,
            }
            return

        if result.content is None or result.content.strip() == "":
            step.status = StepStatus.failed
            step.assessment = CapabilityAssessment.fail
            step.summary = "json_mode returned null or empty content."
            step.details = {
                "http_status": result.http_status,
                "error": result.error,
                "raw_content_preview": None,
                "json_mode_clean": False,
                "schema_valid": False,
            }
            return

        raw = result.content
        raw_preview = raw[:500]
        json_mode_clean = False
        schema_valid = False
        data = None

        # Attempt 1: strict parse (no manipulation — clean json_mode output)
        try:
            data = json.loads(raw)
            json_mode_clean = True
        except json.JSONDecodeError:
            # Attempt 2: try fence-stripped version
            stripped = _extract_fence(raw)
            if stripped:
                try:
                    data = json.loads(stripped)
                except json.JSONDecodeError:
                    pass

        if data is None:
            step.status = StepStatus.failed
            step.assessment = CapabilityAssessment.fail
            step.summary = "json_mode active but response is not valid JSON."
            step.details = {
                "http_status": result.http_status,
                "raw_content_preview": raw_preview,
                "json_mode_clean": False,
                "schema_valid": False,
            }
            return

        # Validate schema
        issues: list[str] = []
        name_ok = isinstance(data.get("name"), str) and len(data.get("name", "").strip()) > 0
        items = data.get("items")
        items_ok = isinstance(items, list) and len(items) > 0
        items_schema_ok = items_ok and all(
            isinstance(it, dict) and "id" in it and "label" in it for it in (items or [])
        )
        if not name_ok:
            issues.append("'name' missing or not a non-empty string")
        if not items_ok:
            issues.append("'items' missing or empty list")
        elif not items_schema_ok:
            issues.append("one or more items missing 'id' or 'label'")

        schema_valid = not bool(issues)

        if json_mode_clean and schema_valid:
            step.status = StepStatus.passed
            step.assessment = CapabilityAssessment.pass_
            step.summary = f"Model returned clean JSON matching schema ({len(items or [])} item(s))."
        elif json_mode_clean and not schema_valid:
            step.status = StepStatus.warning
            step.assessment = CapabilityAssessment.warning
            step.summary = "JSON parsed cleanly but schema incomplete: " + "; ".join(issues)
        else:
            # Fence-wrapped but valid JSON
            step.status = StepStatus.warning
            step.assessment = CapabilityAssessment.warning
            if schema_valid:
                step.summary = "json_mode active but model wrapped response in markdown fences."
            else:
                step.summary = (
                    "json_mode active, model wrapped response in fences, and schema is incomplete: "
                    + "; ".join(issues)
                )

        step.details = {
            "http_status": result.http_status,
            "raw_content_preview": raw_preview,
            "json_mode_clean": json_mode_clean,
            "schema_valid": schema_valid,
            "issues": issues,
            "items_count": len(items) if isinstance(items, list) else 0,
        }

    def _step_long_context_smoke(self, step: CapabilityProbeStepResult) -> None:
        """
        Context retrieval smoke test.
        Builds a synthetic context block with a hidden signal and asks the model to extract it.
        This is NOT a maximum-context certification — it is a basic retrieval sanity check.
        """
        signal = "DELTA-7"
        paragraph = (
            "This document describes the standard delivery framework used across projects. "
            f"The project delivery code is {signal}. "
            "Teams should reference this code in all status updates and milestone reports."
        )
        smoke_size = self._settings.probe_context_smoke_size
        filler = (
            "The delivery team follows an iterative approach with two-week sprint cycles. "
            "Stakeholders review progress at regular cadence meetings held each Friday."
        )
        context_lines = []
        for i in range(smoke_size):
            if i == smoke_size // 2:
                context_lines.append(paragraph)
            else:
                context_lines.append(filler)
        context_text = "\n".join(context_lines)
        context_chars = len(context_text)

        prompt = (
            f"{context_text}\n\n"
            "Based only on the text above, what is the project delivery code? "
            "Reply with the code only."
        )
        messages = [{"role": "user", "content": prompt}]
        result = self._probe_client.send(messages, temperature=0.0, max_tokens=50)

        if result.http_status is not None and result.http_status >= 400:
            step.status = StepStatus.failed
            step.assessment = CapabilityAssessment.fail
            step.summary = f"Context smoke test rejected (HTTP {result.http_status}): {(result.error or '')[:300]}"
            step.details = {
                "context_chars": context_chars,
                "http_status": result.http_status,
                "error": result.error,
            }
            return

        if result.error:
            step.status = StepStatus.failed
            step.assessment = CapabilityAssessment.fail
            step.summary = f"Context smoke test failed: {result.error[:300]}"
            step.details = {"context_chars": context_chars, "error": result.error}
            return

        signal_found = signal in (result.content or "")
        if signal_found:
            step.status = StepStatus.passed
            step.assessment = CapabilityAssessment.pass_
            step.summary = f"Correctly extracted signal from ~{context_chars}-char context."
        elif result.content and result.content.strip():
            step.status = StepStatus.warning
            step.assessment = CapabilityAssessment.warning
            step.summary = "Model responded but did not extract the correct signal."
        else:
            step.status = StepStatus.failed
            step.assessment = CapabilityAssessment.fail
            step.summary = "Model returned null or empty response for context retrieval prompt."

        step.details = {
            "context_chars": context_chars,
            "http_status": result.http_status,
            "signal_found": signal_found,
            "response_preview": (result.content or "")[:100],
        }

    def _step_tool_call_readiness(self, step: CapabilityProbeStepResult) -> None:
        """
        Test whether the integration supports tool/function calling.

        If the API rejects the tools payload with a 4xx error, the integration
        does not expose tool calling — mark as skipped/unknown (not failed).
        A gateway limitation is not a model failure.
        """
        messages = [{"role": "user", "content": "What is the weather in London right now?"}]
        result = self._probe_client.send(
            messages, temperature=0.0, max_tokens=100, tools=[_GET_WEATHER_TOOL]
        )

        if result.http_status is not None and result.http_status >= 400:
            step.status = StepStatus.skipped
            step.assessment = CapabilityAssessment.unknown
            step.summary = f"Tool calling not supported by this integration (HTTP {result.http_status})."
            step.details = {
                "http_status": result.http_status,
                "error": result.error,
                "raw_response_preview": str(result.raw_response or "")[:500],
            }
            return

        if result.error:
            step.status = StepStatus.failed
            step.assessment = CapabilityAssessment.fail
            step.summary = f"Tool call request failed: {result.error[:300]}"
            step.details = {
                "http_status": result.http_status,
                "error": result.error,
            }
            return

        if result.tool_calls:
            first = result.tool_calls[0]
            fn = first.get("function", {})
            tool_name = fn.get("name", "")
            args_preview = str(fn.get("arguments", ""))[:200]
            if tool_name == "get_weather":
                step.status = StepStatus.passed
                step.assessment = CapabilityAssessment.pass_
                step.summary = f"Model correctly invoked tool '{tool_name}'."
            else:
                step.status = StepStatus.warning
                step.assessment = CapabilityAssessment.warning
                step.summary = f"Tool called but unexpected name: '{tool_name}'."
            step.details = {
                "http_status": result.http_status,
                "tool_called": True,
                "tool_name": tool_name,
                "tool_args_preview": args_preview,
                "content_preview": (result.content or "")[:200],
            }
        elif result.content:
            step.status = StepStatus.warning
            step.assessment = CapabilityAssessment.warning
            step.summary = "Model replied in text rather than invoking the tool (tool_choice may be unsupported)."
            step.details = {
                "http_status": result.http_status,
                "tool_called": False,
                "content_preview": result.content[:200],
            }
        else:
            step.status = StepStatus.failed
            step.assessment = CapabilityAssessment.fail
            step.summary = "No tool call and no content returned."
            step.details = {
                "http_status": result.http_status,
                "tool_called": False,
                "raw_response_preview": str(result.raw_response or "")[:500],
            }
