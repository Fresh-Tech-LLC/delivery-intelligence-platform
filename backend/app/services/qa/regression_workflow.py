"""Governed regression-candidate promotion for QA Studio."""
from __future__ import annotations

from datetime import datetime, timezone

from backend.app.services.qa.models import RegressionCandidate, RegressionState

_ALLOWED_TRANSITIONS: dict[RegressionState, set[RegressionState]] = {
    RegressionState.DRAFT: {RegressionState.QA_REVIEWED, RegressionState.DEPRECATED},
    RegressionState.QA_REVIEWED: {RegressionState.EXECUTABLE, RegressionState.DEPRECATED},
    RegressionState.EXECUTABLE: {RegressionState.CANDIDATE_REGRESSION, RegressionState.DEPRECATED},
    RegressionState.CANDIDATE_REGRESSION: {RegressionState.APPROVED_REGRESSION, RegressionState.DEPRECATED},
    RegressionState.APPROVED_REGRESSION: {RegressionState.DEPRECATED},
    RegressionState.DEPRECATED: set(),
}


def promote_candidate(
    candidate: RegressionCandidate,
    target_state: RegressionState,
    rationale: str | None = None,
) -> RegressionCandidate:
    """Transition a regression candidate to a later governed state."""
    allowed = _ALLOWED_TRANSITIONS.get(candidate.state, set())
    if target_state not in allowed:
        raise ValueError(f"Cannot promote regression candidate from {candidate.state.value} to {target_state.value}.")
    candidate.state = target_state
    candidate.rationale = rationale or candidate.rationale
    candidate.updated_at = datetime.now(timezone.utc)
    return candidate
