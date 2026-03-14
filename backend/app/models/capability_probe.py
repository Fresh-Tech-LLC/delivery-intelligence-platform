"""
Pydantic models for the Model Capability Probe feature.

No business logic lives here — pure data definitions.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ProbeStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class StepStatus(str, Enum):
    pending = "pending"
    running = "running"
    passed = "passed"
    warning = "warning"
    failed = "failed"
    skipped = "skipped"


class CapabilityAssessment(str, Enum):
    pass_ = "pass"
    warning = "warning"
    fail = "fail"
    unknown = "unknown"


class FinalRecommendation(str, Enum):
    suitable = "suitable for basic agentic workflows"
    usable = "usable with limitations"
    not_suitable = "not yet suitable"


# ---------------------------------------------------------------------------
# Per-step result
# ---------------------------------------------------------------------------


class CapabilityProbeStepResult(BaseModel):
    name: str
    status: StepStatus = StepStatus.pending
    started_at: str | None = None   # ISO-8601 string
    finished_at: str | None = None
    duration_ms: float | None = None
    assessment: CapabilityAssessment = CapabilityAssessment.unknown
    summary: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Run record
# ---------------------------------------------------------------------------


class CapabilityProbeRun(BaseModel):
    run_id: str
    status: ProbeStatus = ProbeStatus.pending
    started_at: str                          # ISO-8601 string
    finished_at: str | None = None
    current_step: str | None = None
    completed_steps: int = 0
    total_steps: int                         # set from len(PROBE_STEPS) at creation
    steps: list[CapabilityProbeStepResult] = Field(default_factory=list)
    error: str | None = None
    probe_meta: dict[str, Any] = Field(default_factory=dict)  # model, provider, config snapshot


# ---------------------------------------------------------------------------
# Final report
# ---------------------------------------------------------------------------


class CapabilityProbeReport(BaseModel):
    run_id: str
    generated_at: str
    recommendation: FinalRecommendation
    summary: str
    per_capability: dict[str, str]           # step_name → assessment value string
    timings: dict[str, float | None]         # step_name → duration_ms
    raw_results: dict[str, Any]              # compact details per step
    probe_meta: dict[str, Any] = Field(default_factory=dict)
