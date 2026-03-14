"""
Capability probe step execution.

ProbeRunner.run() is a blocking call designed to be called from a daemon thread.
It executes all probe steps sequentially, writing incremental progress to disk
after every step so the polling endpoint always reflects the latest state.
"""
from __future__ import annotations

import logging
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
from backend.app.services.capability_probe.probe_store import ProbeStore
from backend.app.services.llm_client import LLMClient, LLMError

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
        llm_client: LLMClient,
        settings: Settings | None = None,
    ) -> None:
        self._store = store
        self._llm = llm_client
        self._settings = settings or get_settings()

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
        t0 = time.monotonic()
        try:
            response = self._llm.chat(messages, temperature=0.0, max_tokens=5)
            latency_ms = round((time.monotonic() - t0) * 1000, 1)
            if response and response.strip():
                step.status = StepStatus.passed
                step.assessment = CapabilityAssessment.pass_
                step.summary = "API reachable and returned a response."
            else:
                step.status = StepStatus.failed
                step.assessment = CapabilityAssessment.fail
                step.summary = "API returned an empty response."
            step.details = {"latency_ms": latency_ms, "response_preview": response[:100] if response else ""}
        except LLMError as exc:
            step.status = StepStatus.failed
            step.assessment = CapabilityAssessment.fail
            step.summary = f"API unreachable or auth failed: {str(exc)[:300]}"
            step.details = {"error": str(exc)[:500]}

    def _step_simple_generation(self, step: CapabilityProbeStepResult) -> None:
        """Basic generation quality and latency check."""
        messages = [
            {"role": "user", "content": "Describe the role of a product owner in one sentence."}
        ]
        t0 = time.monotonic()
        try:
            response = self._llm.chat(messages, temperature=0.7, max_tokens=100)
            latency_ms = round((time.monotonic() - t0) * 1000, 1)
            length = len(response.strip()) if response else 0
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
                step.summary = "Model returned an empty response."
            step.details = {
                "latency_ms": latency_ms,
                "response_length": length,
                "response_preview": (response or "")[:300],
            }
        except LLMError as exc:
            step.status = StepStatus.failed
            step.assessment = CapabilityAssessment.fail
            step.summary = f"Generation failed: {str(exc)[:300]}"
            step.details = {"error": str(exc)[:500]}

    def _step_deterministic_generation(self, step: CapabilityProbeStepResult) -> None:
        """Run the same low-temperature prompt twice and compare outputs."""
        if not self._settings.llm_temperature_supported:
            step.status = StepStatus.skipped
            step.assessment = CapabilityAssessment.unknown
            step.summary = "Skipped: temperature parameter not supported by this integration."
            step.details = {"reason": "llm_temperature_supported=False"}
            return

        prompt = (
            'List three primary colors. Reply with only a JSON array of strings, '
            'e.g. ["red", "green", "blue"].'
        )
        messages = [{"role": "user", "content": prompt}]

        def _normalize(s: str) -> str:
            return " ".join(s.lower().split())

        try:
            run1 = self._llm.chat(messages, temperature=0.0, max_tokens=50)
            run2 = self._llm.chat(messages, temperature=0.0, max_tokens=50)
        except LLMError as exc:
            step.status = StepStatus.failed
            step.assessment = CapabilityAssessment.fail
            step.summary = f"Deterministic generation failed: {str(exc)[:300]}"
            step.details = {"error": str(exc)[:500]}
            return

        n1, n2 = _normalize(run1), _normalize(run2)
        identical = n1 == n2

        if identical:
            step.status = StepStatus.passed
            step.assessment = CapabilityAssessment.pass_
            step.summary = "Both low-temperature runs produced identical normalized output."
        elif run1.strip() and run2.strip():
            # Both non-empty but different — warning, not a hard failure.
            step.status = StepStatus.warning
            step.assessment = CapabilityAssessment.warning
            step.summary = "Low-temperature runs produced different outputs (non-deterministic)."
        else:
            step.status = StepStatus.failed
            step.assessment = CapabilityAssessment.fail
            step.summary = "One or both low-temperature runs returned empty output."

        step.details = {
            "identical": identical,
            "run1_preview": run1[:200],
            "run2_preview": run2[:200],
        }

    def _step_structured_json(self, step: CapabilityProbeStepResult) -> None:
        """Verify the model can emit valid JSON conforming to a simple nested schema."""
        prompt = (
            "Return valid JSON only — no prose, no markdown fences.\n"
            'Schema: {"name": "<non-empty string>", "items": [{"id": <integer>, "label": "<string>"}, ...]}\n'
            "Include at least 2 items. Use any realistic values."
        )
        messages = [{"role": "user", "content": prompt}]
        try:
            data = self._llm.chat_json(messages, temperature=0.1, max_tokens=300)
        except LLMError as exc:
            step.status = StepStatus.failed
            step.assessment = CapabilityAssessment.fail
            step.summary = f"JSON output failed to parse: {str(exc)[:300]}"
            step.details = {"error": str(exc)[:500]}
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

        if not issues:
            step.status = StepStatus.passed
            step.assessment = CapabilityAssessment.pass_
            step.summary = f"Valid JSON with {len(items or [])} item(s) matching schema."
        else:
            step.status = StepStatus.warning
            step.assessment = CapabilityAssessment.warning
            step.summary = "JSON parsed but schema incomplete: " + "; ".join(issues)

        step.details = {
            "parsed_keys": list(data.keys()) if isinstance(data, dict) else [],
            "items_count": len(items) if isinstance(items, list) else 0,
            "schema_valid": not bool(issues),
            "issues": issues,
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
        # Mix the signal paragraph among filler to make it non-trivial.
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

        try:
            response = self._llm.chat(messages, temperature=0.0, max_tokens=20)
            signal_found = signal in (response or "")
            if signal_found:
                step.status = StepStatus.passed
                step.assessment = CapabilityAssessment.pass_
                step.summary = f"Correctly extracted signal from ~{context_chars}-char context."
            elif response and response.strip():
                step.status = StepStatus.warning
                step.assessment = CapabilityAssessment.warning
                step.summary = "Model responded but did not extract the correct signal."
            else:
                step.status = StepStatus.failed
                step.assessment = CapabilityAssessment.fail
                step.summary = "Model returned empty response for context retrieval prompt."
            step.details = {
                "context_chars": context_chars,
                "signal_found": signal_found,
                "response_preview": (response or "")[:100],
            }
        except LLMError as exc:
            step.status = StepStatus.failed
            step.assessment = CapabilityAssessment.fail
            step.summary = f"Context smoke test failed: {str(exc)[:300]}"
            step.details = {"context_chars": context_chars, "error": str(exc)[:500]}

    def _step_tool_call_readiness(self, step: CapabilityProbeStepResult) -> None:
        """
        Test whether the integration supports tool/function calling.

        If the API rejects the tools payload with a 4xx error, the integration
        does not expose tool calling — mark as skipped/unknown (not failed).
        A wrapper limitation is not a model failure.
        """
        messages = [{"role": "user", "content": "What is the weather in London right now?"}]
        try:
            message = self._llm.chat_with_tools(
                messages,
                tools=[_GET_WEATHER_TOOL],
                temperature=0.0,
                max_tokens=100,
            )
        except LLMError as exc:
            err_str = str(exc)
            # 4xx errors indicate the integration doesn't support the tools param.
            if any(f"HTTP error {c}" in err_str or f"HTTP {c}" in err_str
                   for c in ["400", "422", "415", "501"]):
                step.status = StepStatus.skipped
                step.assessment = CapabilityAssessment.unknown
                step.summary = "Tool calling is not exposed by the current integration."
                step.details = {"reason": "API rejected tools payload", "error": err_str[:300]}
            else:
                step.status = StepStatus.failed
                step.assessment = CapabilityAssessment.fail
                step.summary = f"Tool call request failed unexpectedly: {err_str[:300]}"
                step.details = {"error": err_str[:500]}
            return

        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            first = tool_calls[0]
            fn = first.get("function", {})
            tool_name = fn.get("name", "")
            args_preview = str(fn.get("arguments", ""))[:200]
            called_correctly = tool_name == "get_weather"
            if called_correctly:
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
                "tool_args_preview": args_preview,
            }
        else:
            # Model replied in text instead of using the tool.
            content_preview = str(message.get("content") or "")[:200]
            step.status = StepStatus.warning
            step.assessment = CapabilityAssessment.warning
            step.summary = "Model replied in text rather than invoking the tool."
            step.details = {
                "tool_called": False,
                "response_preview": content_preview,
            }
