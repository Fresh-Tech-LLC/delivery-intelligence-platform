"""Evidence persistence for QA Studio."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from backend.app.config import get_settings
from backend.app.services.qa.models import ExecutionEvidence


class EvidenceStore:
    """Persist QA evidence assets and metadata under the configured evidence directory."""

    def __init__(self) -> None:
        self._root = get_settings().qa_playwright_evidence_dir

    def save_text_summary(
        self,
        qa_workspace_id: str,
        related_run_id: str | None,
        summary: str,
        evidence_type: str = "text_summary",
    ) -> ExecutionEvidence:
        evidence_dir = self._root / qa_workspace_id
        evidence_dir.mkdir(parents=True, exist_ok=True)
        evidence_id = self._evidence_id(qa_workspace_id, related_run_id, evidence_type, summary)
        path = evidence_dir / f"{evidence_id}.txt"
        path.write_text(summary, encoding="utf-8")
        return ExecutionEvidence(
            evidence_id=evidence_id,
            qa_workspace_id=qa_workspace_id,
            related_run_id=related_run_id,
            evidence_type=evidence_type,
            file_path=str(path),
            summary=summary[:240],
            created_at=datetime.now(timezone.utc),
            extra={},
        )

    def _evidence_id(self, qa_workspace_id: str, related_run_id: str | None, evidence_type: str, summary: str) -> str:
        digest = hashlib.sha1(f"{qa_workspace_id}|{related_run_id}|{evidence_type}|{summary}".encode("utf-8")).hexdigest()[:12]
        return f"evidence-{digest}"
