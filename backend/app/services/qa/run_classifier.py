"""Deterministic failure classification for QA Studio execution results."""
from __future__ import annotations

from backend.app.services.qa.models import FailureCategory


def classify_failure(status: str, summary: str | None = None) -> FailureCategory | None:
    """Classify a failure summary into a stable FailureCategory."""
    if status == "completed":
        return None
    text = (summary or "").lower()
    if "selector" in text or "timeout" in text or "wait" in text:
        return FailureCategory.SELECTOR_OR_SYNC_ISSUE
    if "data" in text or "fixture" in text or "setup" in text:
        return FailureCategory.TEST_DATA_ISSUE
    if "env" in text or "network" in text or "unavailable" in text:
        return FailureCategory.ENVIRONMENT_ISSUE
    if "appian" in text or "defect" in text:
        return FailureCategory.APPIAN_DEFECT
    if "requirement" in text or "ambiguous" in text:
        return FailureCategory.REQUIREMENT_AMBIGUITY
    if "expected behavior" in text or "changed" in text:
        return FailureCategory.EXPECTED_BEHAVIOR_CHANGE
    if "logic" in text or "generated" in text:
        return FailureCategory.GENERATED_TEST_LOGIC_ISSUE
    return FailureCategory.UNKNOWN
