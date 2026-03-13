from __future__ import annotations

"""Derived workflow state for the Requirements Studio UI."""

from typing import Literal

from pydantic import BaseModel, Field

from backend.app.services.requirements.models import (
    BacklogDraft,
    ContextPack,
    FeatureWorkspace,
    RequirementsDraft,
)

WorkflowStageName = Literal[
    "create_workspace",
    "build_context",
    "pin_evidence",
    "generate_requirements",
    "review_edit_requirements",
    "generate_backlog",
    "review_export_publish",
]

StageStatus = Literal["completed", "current", "upcoming"]


class WorkflowStageState(BaseModel):
    """UI-friendly status for a single workflow stage."""

    stage: WorkflowStageName
    label: str
    status: StageStatus
    detail: str


class WorkflowState(BaseModel):
    """Derived workflow state for a requirements workspace."""

    current_stage: WorkflowStageName
    completed_stages: list[WorkflowStageName] = Field(default_factory=list)
    next_stage: WorkflowStageName | None = None
    recommended_next_action: str
    blocking_items: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    progress_percent: int
    stage_statuses: list[WorkflowStageState] = Field(default_factory=list)


_STAGE_ORDER: list[tuple[WorkflowStageName, str]] = [
    ("create_workspace", "Create Workspace"),
    ("build_context", "Build Context"),
    ("pin_evidence", "Pin Evidence"),
    ("generate_requirements", "Generate Requirements"),
    ("review_edit_requirements", "Review/Edit"),
    ("generate_backlog", "Generate Backlog"),
    ("review_export_publish", "Review/Export/Publish"),
]


def derive_workflow_state(
    workspace: FeatureWorkspace,
    context_pack: ContextPack | None,
    requirements_draft: RequirementsDraft | None,
    backlog_draft: BacklogDraft | None,
) -> WorkflowState:
    """Derive a deterministic workflow state from persisted records."""

    has_context = context_pack is not None
    has_pinned = bool(workspace.pinned_evidence)
    has_requirements = requirements_draft is not None
    has_backlog = backlog_draft is not None
    has_review_content = bool(workspace.assumptions or workspace.open_questions)

    current_stage: WorkflowStageName
    next_stage: WorkflowStageName | None
    completed_stages: list[WorkflowStageName] = ["create_workspace"]
    blocking_items: list[str] = []
    warnings: list[str] = []

    if has_context:
        completed_stages.append("build_context")
    else:
        current_stage = "build_context"
        next_stage = "build_context"
        blocking_items.append("Build a context pack before generating grounded outputs.")
        return _build_state(
            workspace=workspace,
            context_pack=context_pack,
            requirements_draft=requirements_draft,
            backlog_draft=backlog_draft,
            current_stage=current_stage,
            next_stage=next_stage,
            completed_stages=completed_stages,
            blocking_items=blocking_items,
            warnings=warnings,
        )

    if has_pinned:
        completed_stages.append("pin_evidence")
    else:
        warnings.append("No pinned evidence yet. Outputs can still be generated, but the grounding is thinner.")

    if has_requirements:
        completed_stages.append("generate_requirements")
        if has_review_content:
            completed_stages.append("review_edit_requirements")
    if has_backlog:
        completed_stages.append("generate_backlog")

    if not has_pinned:
        current_stage = "pin_evidence"
        next_stage = "pin_evidence"
        blocking_items.append("Pin at least one artifact or chunk to make the evidence trail easier to review.")
    elif not has_requirements:
        current_stage = "generate_requirements"
        next_stage = "generate_requirements"
        blocking_items.append("Generate a requirements draft before moving to review and backlog decomposition.")
    elif not has_review_content:
        current_stage = "review_edit_requirements"
        next_stage = "review_edit_requirements"
        warnings.append("Requirements have been generated, but assumptions/open questions still need review.")
    elif not has_backlog:
        current_stage = "generate_backlog"
        next_stage = "generate_backlog"
        blocking_items.append("Generate a backlog draft to prepare for downstream Jira/export handoff.")
    else:
        current_stage = "review_export_publish"
        next_stage = None
        warnings.append("Direct Jira publish is not implemented yet. Use export/download for downstream handoff.")

    if context_pack is not None:
        if not context_pack.search_hits:
            warnings.append("No lexical search hits were found for the current request.")
        if not context_pack.related_hits:
            warnings.append("No related artifacts were found from the current context.")
        if not _has_appian_context(workspace, context_pack):
            warnings.append("No Appian artifacts are currently grounding this workspace.")

    return _build_state(
        workspace=workspace,
        context_pack=context_pack,
        requirements_draft=requirements_draft,
        backlog_draft=backlog_draft,
        current_stage=current_stage,
        next_stage=next_stage,
        completed_stages=completed_stages,
        blocking_items=blocking_items,
        warnings=warnings,
    )


def _build_state(
    *,
    workspace: FeatureWorkspace,
    context_pack: ContextPack | None,
    requirements_draft: RequirementsDraft | None,
    backlog_draft: BacklogDraft | None,
    current_stage: WorkflowStageName,
    next_stage: WorkflowStageName | None,
    completed_stages: list[WorkflowStageName],
    blocking_items: list[str],
    warnings: list[str],
) -> WorkflowState:
    stage_statuses: list[WorkflowStageState] = []
    current_index = next(
        (index for index, (stage, _) in enumerate(_STAGE_ORDER) if stage == current_stage),
        len(_STAGE_ORDER) - 1,
    )
    for index, (stage, label) in enumerate(_STAGE_ORDER):
        if stage in completed_stages and stage != current_stage:
            status: StageStatus = "completed"
        elif index == current_index:
            status = "current"
        else:
            status = "upcoming"
        stage_statuses.append(
            WorkflowStageState(
                stage=stage,
                label=label,
                status=status,
                detail=_stage_detail(stage, workspace, context_pack, requirements_draft, backlog_draft),
            )
        )

    progress_percent = max(14, round((len(completed_stages) / len(_STAGE_ORDER)) * 100))
    return WorkflowState(
        current_stage=current_stage,
        completed_stages=completed_stages,
        next_stage=next_stage,
        recommended_next_action=_next_action_message(current_stage),
        blocking_items=list(dict.fromkeys(blocking_items)),
        warnings=list(dict.fromkeys(warnings)),
        progress_percent=progress_percent,
        stage_statuses=stage_statuses,
    )


def _stage_detail(
    stage: WorkflowStageName,
    workspace: FeatureWorkspace,
    context_pack: ContextPack | None,
    requirements_draft: RequirementsDraft | None,
    backlog_draft: BacklogDraft | None,
) -> str:
    if stage == "create_workspace":
        return f"Workspace {workspace.workspace_id} created for {workspace.title}."
    if stage == "build_context":
        if context_pack is None:
            return "Build the first context pack from search and related-artifact signals."
        return f"{len(context_pack.search_hits)} search hits, {len(context_pack.related_hits)} related artifacts."
    if stage == "pin_evidence":
        return f"{len(workspace.pinned_evidence)} evidence items pinned."
    if stage == "generate_requirements":
        if requirements_draft is None:
            return "Generate a structured requirements draft from the current context."
        return f"{len(requirements_draft.requirements)} requirement items generated."
    if stage == "review_edit_requirements":
        assumption_count = len(workspace.assumptions)
        question_count = len(workspace.open_questions)
        return f"{assumption_count} assumptions and {question_count} open questions currently tracked."
    if stage == "generate_backlog":
        if backlog_draft is None:
            return "Generate a backlog draft after reviewing the requirements output."
        return f"{len(backlog_draft.items)} backlog items drafted in {backlog_draft.split_mode} mode."
    return "Export JSON outputs for handoff. Direct Jira publish is intentionally deferred."


def _next_action_message(stage: WorkflowStageName) -> str:
    messages: dict[WorkflowStageName, str] = {
        "create_workspace": "Create a workspace to start the BA flow.",
        "build_context": "Build context to ground the workspace in existing artifacts and links.",
        "pin_evidence": "Pin the most relevant artifact or chunk references you want reviewers to trust.",
        "generate_requirements": "Generate a structured requirements draft from the current evidence set.",
        "review_edit_requirements": "Review assumptions, open questions, and the top-level draft framing before backlog decomposition.",
        "generate_backlog": "Generate a backlog draft once the requirements framing is acceptable.",
        "review_export_publish": "Review the outputs and export JSON for downstream handoff. Jira publish is not available yet.",
    }
    return messages[stage]


def _has_appian_context(workspace: FeatureWorkspace, context_pack: ContextPack) -> bool:
    if workspace.project_key and workspace.project_key.upper().startswith("APP"):
        appian_items = [
            item
            for item in [*context_pack.search_hits, *context_pack.related_hits, *context_pack.pinned_items]
            if getattr(item, "source_system", None) == "appian"
        ]
        return bool(appian_items)
    return True
