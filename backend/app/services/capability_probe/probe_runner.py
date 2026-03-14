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
from backend.app.services.capability_probe.probe_store import ProbeStore
from backend.app.services.llm_adapters import (
    AdapterInvocationError,
    AdapterResponse,
    AdapterUnsupportedFeatureError,
    LLMAdapter,
    get_llm_adapter,
)

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

# Schema used by the structured_json_output step.
_JSON_STEP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "label": {"type": "string"},
                },
                "required": ["id", "label"],
            },
        },
    },
    "required": ["name", "items"],
}


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
        llm_client: Any = None,  # kept for backwards compat — unused
        settings: Settings | None = None,
        adapter: LLMAdapter | None = None,
    ) -> None:
        self._store = store
        self._settings = settings or get_settings()
        self._adapter: LLMAdapter = adapter or get_llm_adapter()

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
        run.probe_meta = {
            "adapter_name": self._adapter.get_name(),
            "model_name": self._adapter.get_model_name(),
            "adapter_type": self._settings.llm_adapter_type,
            "probe_context_smoke_size": self._settings.probe_context_smoke_size,
        }
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
        try:
            response = self._adapter.ask_text(
                "Reply with the single word OK.",
                temperature=0.0,
                max_tokens=5,
            )
        except AdapterInvocationError as exc:
            step.status = StepStatus.failed
            step.assessment = CapabilityAssessment.fail
            step.summary = f"API unreachable or auth failed: {str(exc)[:300]}"
            step.details = {"error": str(exc)[:500]}
            return

        if response.text.strip():
            step.status = StepStatus.passed
            step.assessment = CapabilityAssessment.pass_
            step.summary = "API reachable and returned a response."
        else:
            step.status = StepStatus.failed
            step.assessment = CapabilityAssessment.fail
            step.summary = "API returned null or empty content."

        step.details = {
            "latency_ms": response.latency_ms,
            "response_preview": response.text[:200],
        }

    def _step_simple_generation(self, step: CapabilityProbeStepResult) -> None:
        """Basic generation quality and latency check."""
        try:
            response = self._adapter.ask_text(
                "Describe the role of a product owner in one sentence.",
                temperature=0.7,
                max_tokens=100,
            )
        except AdapterInvocationError as exc:
            step.status = StepStatus.failed
            step.assessment = CapabilityAssessment.fail
            step.summary = f"Generation failed: {str(exc)[:300]}"
            step.details = {"error": str(exc)[:500]}
            return

        length = len(response.text.strip())
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
            "latency_ms": response.latency_ms,
            "response_length": length,
            "response_preview": response.text[:300],
        }

    def _step_deterministic_generation(self, step: CapabilityProbeStepResult) -> None:
        """Run the same low-temperature prompt twice and compare outputs."""
        prompt = (
            'List three primary colors. Reply with only a JSON array of strings, '
            'e.g. ["red", "green", "blue"].'
        )

        def _normalize(s: str) -> str:
            return " ".join(s.lower().split())

        try:
            r1 = self._adapter.ask_text(prompt, temperature=0.0, max_tokens=150)
            r2 = self._adapter.ask_text(prompt, temperature=0.0, max_tokens=150)
        except AdapterInvocationError as exc:
            step.status = StepStatus.failed
            step.assessment = CapabilityAssessment.fail
            step.summary = f"Deterministic generation failed: {str(exc)[:300]}"
            step.details = {"error": str(exc)[:500]}
            return

        if not r1.text.strip() or not r2.text.strip():
            step.status = StepStatus.failed
            step.assessment = CapabilityAssessment.fail
            step.summary = "One or both low-temperature runs returned empty output."
            step.details = {"run1_preview": r1.text[:200], "run2_preview": r2.text[:200]}
            return

        identical = _normalize(r1.text) == _normalize(r2.text)
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
            "run1_preview": r1.text[:200],
            "run2_preview": r2.text[:200],
        }

    def _step_structured_json(self, step: CapabilityProbeStepResult) -> None:
        """
        Verify the model can emit valid JSON when structured output is requested.

        Assessment:
        - pass    : response parses as valid JSON directly (no fences, no repair)
        - warning : response wrapped in markdown fences but contains valid JSON inside,
                    OR JSON parses but schema is incomplete
        - fail    : invocation error, null/empty content, or not valid JSON
        - unknown : adapter declares structured_output=False
        """
        support = self._adapter.get_feature_support()
        if not support.structured_output:
            step.status = StepStatus.skipped
            step.assessment = CapabilityAssessment.unknown
            step.summary = "Structured output is not supported by the selected adapter."
            return

        prompt = (
            "Return valid JSON only — no prose, no markdown fences.\n"
            'Schema: {"name": "<non-empty string>", "items": [{"id": <integer>, "label": "<string>"}, ...]}\n'
            "Include at least 2 items. Use any realistic values."
        )

        try:
            response = self._adapter.ask_structured(
                prompt, _JSON_STEP_SCHEMA, temperature=0.1, max_tokens=300
            )
        except AdapterUnsupportedFeatureError as exc:
            step.status = StepStatus.skipped
            step.assessment = CapabilityAssessment.unknown
            step.summary = str(exc)[:300]
            return
        except AdapterInvocationError as exc:
            step.status = StepStatus.failed
            step.assessment = CapabilityAssessment.fail
            step.summary = str(exc)[:300]
            return

        raw = response.text
        raw_preview = raw[:500]

        if not raw.strip():
            step.status = StepStatus.failed
            step.assessment = CapabilityAssessment.fail
            step.summary = "Structured output returned null or empty content."
            step.details = {"raw_content_preview": None, "json_mode_clean": False, "schema_valid": False}
            return

        # Determine if JSON is clean (no fences) or fence-wrapped.
        json_mode_clean = response.parsed_json is not None
        data = response.parsed_json

        if data is None:
            # Adapter could not parse — try fence-strip as fallback.
            stripped = _extract_fence(raw)
            if stripped:
                try:
                    data = json.loads(stripped)
                except json.JSONDecodeError:
                    pass

        if data is None:
            step.status = StepStatus.failed
            step.assessment = CapabilityAssessment.fail
            step.summary = "Structured output request active but response is not valid JSON."
            step.details = {
                "raw_content_preview": raw_preview,
                "json_mode_clean": False,
                "schema_valid": False,
            }
            return

        # Validate schema.
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
        elif schema_valid:
            step.status = StepStatus.warning
            step.assessment = CapabilityAssessment.warning
            step.summary = "Structured output active but model wrapped response in markdown fences."
        else:
            step.status = StepStatus.warning
            step.assessment = CapabilityAssessment.warning
            step.summary = (
                "Structured output active, model wrapped in fences, and schema incomplete: "
                + "; ".join(issues)
            )

        step.details = {
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
            "Based only on the text above, what is the project delivery code? "
            "Reply with the code only."
        )

        try:
            # Pass context via the adapter's context param (ModelEngine uses it natively;
            # OpenAI adapter prepends it to the prompt).
            response = self._adapter.ask_text(
                prompt,
                temperature=0.0,
                max_tokens=50,
                context=context_text,
            )
        except AdapterInvocationError as exc:
            step.status = StepStatus.failed
            step.assessment = CapabilityAssessment.fail
            step.summary = f"Context smoke test failed: {str(exc)[:300]}"
            step.details = {"context_chars": context_chars, "error": str(exc)[:500]}
            return

        signal_found = signal in response.text
        if signal_found:
            step.status = StepStatus.passed
            step.assessment = CapabilityAssessment.pass_
            step.summary = f"Correctly extracted signal from ~{context_chars}-char context."
        elif response.text.strip():
            step.status = StepStatus.warning
            step.assessment = CapabilityAssessment.warning
            step.summary = "Model responded but did not extract the correct signal."
        else:
            step.status = StepStatus.failed
            step.assessment = CapabilityAssessment.fail
            step.summary = "Model returned null or empty response for context retrieval prompt."

        step.details = {
            "context_chars": context_chars,
            "signal_found": signal_found,
            "response_preview": response.text[:100],
            "latency_ms": response.latency_ms,
        }

    def _step_tool_call_readiness(self, step: CapabilityProbeStepResult) -> None:
        """
        Test whether the integration supports tool/function calling.

        If the adapter declares tool_calling=False, mark as skipped/unknown.
        If the adapter raises AdapterUnsupportedFeatureError, mark skipped/unknown.
        If the request fails (AdapterInvocationError), mark as fail.
        Otherwise assess response.tool_calls honestly.
        """
        support = self._adapter.get_feature_support()
        if not support.tool_calling:
            step.status = StepStatus.skipped
            step.assessment = CapabilityAssessment.unknown
            step.summary = "Tool calling is not supported by the selected adapter."
            return

        try:
            response = self._adapter.ask_with_tools(
                "What is the weather in London right now?",
                tools=[_GET_WEATHER_TOOL],
                temperature=0.0,
                max_tokens=100,
            )
        except AdapterUnsupportedFeatureError as exc:
            step.status = StepStatus.skipped
            step.assessment = CapabilityAssessment.unknown
            step.summary = str(exc)[:300]
            return
        except AdapterInvocationError as exc:
            step.status = StepStatus.failed
            step.assessment = CapabilityAssessment.fail
            step.summary = f"Tool call request failed: {str(exc)[:300]}"
            step.details = {"error": str(exc)[:500]}
            return

        if response.tool_calls:
            tool_name = response.tool_calls[0].name
            if tool_name == "get_weather":
                step.status = StepStatus.passed
                step.assessment = CapabilityAssessment.pass_
                step.summary = f"Model correctly invoked tool '{tool_name}'."
            else:
                step.status = StepStatus.warning
                step.assessment = CapabilityAssessment.warning
                step.summary = f"Tool called but unexpected name: '{tool_name}'."
            step.details = {
                "tool_called": True,
                "tool_name": tool_name,
                "tool_args": response.tool_calls[0].arguments,
                "content_preview": response.text[:200],
                "latency_ms": response.latency_ms,
            }
        elif response.text:
            step.status = StepStatus.warning
            step.assessment = CapabilityAssessment.warning
            step.summary = "Model replied in text rather than invoking the tool (tool_choice may be unsupported)."
            step.details = {
                "tool_called": False,
                "content_preview": response.text[:200],
                "latency_ms": response.latency_ms,
            }
        else:
            step.status = StepStatus.failed
            step.assessment = CapabilityAssessment.fail
            step.summary = "No tool call and no content returned."
            step.details = {"tool_called": False, "latency_ms": response.latency_ms}
