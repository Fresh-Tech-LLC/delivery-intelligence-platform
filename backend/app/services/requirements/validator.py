from __future__ import annotations

"""Rule-based validation for Requirements Studio workspaces."""

import uuid
from datetime import datetime, timezone

from backend.app.services.requirements.models import BacklogDraft, FeatureWorkspace, RequirementsDraft
from backend.app.services.requirements.state_models import (
    ContextSnapshot,
    ValidationIssue,
    ValidationResult,
    ValidationSeverity,
    ValidationTargetType,
)


class WorkspaceValidator:
    """Build deterministic validation results from current workspace state."""

    def validate(
        self,
        workspace: FeatureWorkspace,
        *,
        context_snapshot: ContextSnapshot | None,
        requirements_draft: RequirementsDraft | None,
        backlog_draft: BacklogDraft | None,
        target_type: ValidationTargetType = ValidationTargetType.WORKSPACE,
        target_id: str | None = None,
    ) -> ValidationResult:
        issues: list[ValidationIssue] = []

        if context_snapshot is None:
            issues.append(
                self._issue(
                    severity=ValidationSeverity.ERROR,
                    category="missing_evidence",
                    title="No context snapshot exists",
                    description="Build context before relying on generated outputs.",
                )
            )
        else:
            if not context_snapshot.search_hits and not context_snapshot.related_hits:
                issues.append(
                    self._issue(
                        severity=ValidationSeverity.WARNING,
                        category="limited_context",
                        title="Context snapshot produced no useful hits",
                        description="The latest context snapshot did not find strong search or graph leads.",
                    )
                )

        if not workspace.pinned_evidence:
            issues.append(
                self._issue(
                    severity=ValidationSeverity.WARNING,
                    category="missing_evidence",
                    title="No pinned evidence",
                    description="Pin at least one artifact or chunk to strengthen review traceability.",
                )
            )

        if requirements_draft is None and backlog_draft is not None:
            issues.append(
                self._issue(
                    severity=ValidationSeverity.ERROR,
                    category="workflow_gap",
                    title="Backlog exists without requirements",
                    description="A backlog draft should not be the only structured output.",
                )
            )

        if requirements_draft is not None:
            for requirement in requirements_draft.requirements:
                if not requirement.source_refs:
                    issues.append(
                        self._issue(
                            severity=ValidationSeverity.WARNING,
                            category="unsupported_claim",
                            title=f"{requirement.requirement_id} has no source refs",
                            description="Each requirement item should point back to supporting evidence.",
                            linked_refs=[requirement.requirement_id],
                        )
                    )
                if not requirement.acceptance_criteria:
                    issues.append(
                        self._issue(
                            severity=ValidationSeverity.WARNING,
                            category="missing_ac",
                            title=f"{requirement.requirement_id} has no acceptance criteria",
                            description="Acceptance criteria are needed for review, backlog quality, and future QA traceability.",
                            linked_refs=[requirement.requirement_id],
                        )
                    )

        if backlog_draft is not None:
            for item in backlog_draft.items:
                if not item.source_refs:
                    issues.append(
                        self._issue(
                            severity=ValidationSeverity.WARNING,
                            category="orphan_backlog_item",
                            title=f"{item.item_id} has no source refs",
                            description="Backlog items should link back to the requirements or context that justify them.",
                            linked_refs=[item.item_id],
                        )
                    )

        unresolved_count = len(workspace.assumptions) + len(workspace.open_questions)
        if unresolved_count >= 6:
            issues.append(
                self._issue(
                    severity=ValidationSeverity.INFO,
                    category="ambiguity",
                    title="Many assumptions or open questions remain",
                    description="This workspace still contains several unresolved analyst notes that may need review before export.",
                )
            )

        if workspace.project_key and workspace.project_key.upper().startswith("APP"):
            snapshot_refs = context_snapshot.selected_source_refs if context_snapshot else []
            if not any("appian" in ref_id for ref_id in snapshot_refs):
                issues.append(
                    self._issue(
                        severity=ValidationSeverity.INFO,
                        category="limited_appian_context",
                        title="No Appian refs in latest context",
                        description="This looks like an Appian-oriented workspace, but the latest context snapshot does not reference Appian artifacts.",
                    )
                )

        summary = f"{len(issues)} validation issue(s) found." if issues else "No validation issues found."
        return ValidationResult(
            validation_result_id=f"validation-{uuid.uuid4().hex}",
            workspace_id=workspace.workspace_id,
            created_at=datetime.now(timezone.utc),
            target_type=target_type,
            target_id=target_id or workspace.workspace_id,
            issues=issues,
            summary=summary,
        )

    def _issue(
        self,
        *,
        severity: ValidationSeverity,
        category: str,
        title: str,
        description: str,
        linked_refs: list[str] | None = None,
    ) -> ValidationIssue:
        return ValidationIssue(
            issue_id=f"issue-{uuid.uuid4().hex[:12]}",
            severity=severity,
            category=category,
            title=title,
            description=description,
            linked_refs=linked_refs or [],
        )


def get_workspace_validator() -> WorkspaceValidator:
    return WorkspaceValidator()
