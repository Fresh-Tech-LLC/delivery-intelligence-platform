"""Phase 4 orchestration service for Requirements Studio."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from functools import lru_cache
import uuid

from backend.app.config import get_settings
from backend.app.services.graph.models import ArtifactRecord, ChunkRecord
from backend.app.services.knowledge_service import get_knowledge_service
from backend.app.services.llm_client import get_llm_client
from backend.app.services.prompt_loader import get_prompt_loader
from backend.app.services.requirements.backlog_generator import BacklogGenerator
from backend.app.services.requirements.context_pack_builder import ContextPackBuilder
from backend.app.services.requirements.models import (
    BacklogDraft,
    ContextPack,
    FeatureWorkspace,
    RequirementsDraft,
    WorkspaceEvidenceItem,
    WorkspaceEvidenceType,
    WorkspaceStatus,
)
from backend.app.services.requirements.requirements_generator import RequirementsGenerator
from backend.app.services.requirements.workflow import WorkflowState, derive_workflow_state
from backend.app.services.requirements.workspace_store import WorkspaceStore, get_workspace_store


class RequirementsService:
    """Thin orchestration layer for Requirements Studio operations."""

    def __init__(self, store: WorkspaceStore | None = None) -> None:
        self._store = store if store is not None else get_workspace_store()
        self._settings = get_settings()
        self._knowledge = get_knowledge_service()

    def create_workspace(self, title: str, request_text: str, project_key: str | None = None) -> FeatureWorkspace:
        if not title.strip():
            raise ValueError("Workspace title must not be empty.")
        if not request_text.strip():
            raise ValueError("Workspace request_text must not be empty.")
        now = datetime.now(timezone.utc)
        workspace = FeatureWorkspace(
            workspace_id=f"ws-{uuid.uuid4().hex}",
            title=title.strip(),
            project_key=(project_key or "").strip() or None,
            request_text=request_text.strip(),
            request_summary=self._summarize_request(request_text),
            created_at=now,
            updated_at=now,
        )
        self._store.save_workspace(workspace)
        return workspace

    def list_workspaces(self) -> list[FeatureWorkspace]:
        return self._store.list_workspaces()

    def get_workspace(self, workspace_id: str) -> FeatureWorkspace | None:
        return self._store.get_workspace(workspace_id)

    def get_context_pack(self, workspace_id: str) -> ContextPack | None:
        return self._store.get_context_pack(workspace_id)

    def get_workflow_state(self, workspace_id: str) -> WorkflowState:
        workspace = self._require_workspace(workspace_id)
        return derive_workflow_state(
            workspace,
            self.get_context_pack(workspace_id),
            self.get_requirements_draft(workspace_id),
            self.get_backlog_draft(workspace_id),
        )

    def build_context_pack(self, workspace_id: str) -> ContextPack:
        workspace = self._require_workspace(workspace_id)
        context_pack = ContextPackBuilder(self._knowledge).build(workspace)
        self._store.save_context_pack(context_pack)
        workspace.generated_context_summary = context_pack.summary_text
        if workspace.status == WorkspaceStatus.DRAFT:
            workspace.status = WorkspaceStatus.READY_FOR_REQUIREMENTS
        workspace.updated_at = datetime.now(timezone.utc)
        self._store.save_workspace(workspace)
        return context_pack

    def pin_evidence(
        self,
        workspace_id: str,
        ref_id: str,
        rationale: str | None = None,
        title: str | None = None,
    ) -> FeatureWorkspace:
        workspace = self._require_workspace(workspace_id)
        item = self._build_evidence_item(ref_id, rationale, title_override=title)
        if any(existing.evidence_id == item.evidence_id for existing in workspace.pinned_evidence):
            return workspace
        if len(workspace.pinned_evidence) >= self._settings.requirements_context_max_pinned_items:
            raise ValueError(
                f"Workspace already has the maximum of {self._settings.requirements_context_max_pinned_items} pinned items."
            )
        workspace.pinned_evidence.append(item)
        workspace.updated_at = datetime.now(timezone.utc)
        self._store.save_workspace(workspace)
        return workspace

    def unpin_evidence(self, workspace_id: str, evidence_id: str) -> FeatureWorkspace:
        workspace = self._require_workspace(workspace_id)
        workspace.pinned_evidence = [
            item for item in workspace.pinned_evidence if item.evidence_id != evidence_id
        ]
        workspace.updated_at = datetime.now(timezone.utc)
        self._store.save_workspace(workspace)
        return workspace

    def generate_requirements(self, workspace_id: str) -> RequirementsDraft:
        workspace = self._require_workspace(workspace_id)
        context_pack = self.get_context_pack(workspace_id) or self.build_context_pack(workspace_id)
        draft = RequirementsGenerator(get_llm_client(), get_prompt_loader()).generate(workspace, context_pack)
        self._store.save_requirements_draft(draft)
        workspace.status = WorkspaceStatus.REQUIREMENTS_GENERATED
        workspace.assumptions = list(draft.assumptions)
        workspace.open_questions = list(draft.open_questions)
        workspace.updated_at = datetime.now(timezone.utc)
        self._store.save_workspace(workspace)
        return draft

    def save_requirements_draft(self, workspace_id: str, raw_json: str) -> RequirementsDraft:
        workspace = self._require_workspace(workspace_id)
        draft = RequirementsDraft(**json.loads(raw_json))
        if draft.workspace_id != workspace.workspace_id:
            raise ValueError("Requirements draft workspace_id does not match the target workspace.")
        self._store.save_requirements_draft(draft)
        workspace.status = WorkspaceStatus.REQUIREMENTS_GENERATED
        workspace.assumptions = list(draft.assumptions)
        workspace.open_questions = list(draft.open_questions)
        workspace.updated_at = datetime.now(timezone.utc)
        self._store.save_workspace(workspace)
        return draft

    def generate_backlog(self, workspace_id: str, split_mode: str | None = None) -> BacklogDraft:
        workspace = self._require_workspace(workspace_id)
        requirements_draft = self.get_requirements_draft(workspace_id)
        if requirements_draft is None:
            raise ValueError("Generate requirements before generating a backlog.")
        context_pack = self.get_context_pack(workspace_id)
        draft = BacklogGenerator(get_llm_client(), get_prompt_loader()).generate(
            workspace,
            requirements_draft,
            context_pack=context_pack,
            split_mode=split_mode,
        )
        self._store.save_backlog_draft(draft)
        workspace.status = WorkspaceStatus.BACKLOG_GENERATED
        workspace.updated_at = datetime.now(timezone.utc)
        self._store.save_workspace(workspace)
        return draft

    def save_backlog_draft(self, workspace_id: str, raw_json: str) -> BacklogDraft:
        workspace = self._require_workspace(workspace_id)
        draft = BacklogDraft(**json.loads(raw_json))
        if draft.workspace_id != workspace.workspace_id:
            raise ValueError("Backlog draft workspace_id does not match the target workspace.")
        self._store.save_backlog_draft(draft)
        workspace.status = WorkspaceStatus.BACKLOG_GENERATED
        workspace.updated_at = datetime.now(timezone.utc)
        self._store.save_workspace(workspace)
        return draft

    def get_requirements_draft(self, workspace_id: str) -> RequirementsDraft | None:
        return self._store.get_requirements_draft(workspace_id)

    def get_backlog_draft(self, workspace_id: str) -> BacklogDraft | None:
        return self._store.get_backlog_draft(workspace_id)

    def update_review_fields(
        self,
        workspace_id: str,
        *,
        assumptions_text: str | None = None,
        open_questions_text: str | None = None,
        problem_statement: str | None = None,
        business_outcome: str | None = None,
        requirements_generation_notes: str | None = None,
    ) -> tuple[FeatureWorkspace, RequirementsDraft | None]:
        workspace = self._require_workspace(workspace_id)
        requirements_draft = self.get_requirements_draft(workspace_id)

        if assumptions_text is not None:
            workspace.assumptions = self._split_lines(assumptions_text)
        if open_questions_text is not None:
            workspace.open_questions = self._split_lines(open_questions_text)
        workspace.updated_at = datetime.now(timezone.utc)
        self._store.save_workspace(workspace)

        if requirements_draft is not None:
            if problem_statement is not None:
                requirements_draft.problem_statement = problem_statement.strip()
            if business_outcome is not None:
                requirements_draft.business_outcome = business_outcome.strip()
            if requirements_generation_notes is not None:
                requirements_draft.generation_notes = requirements_generation_notes.strip() or None
            requirements_draft.assumptions = list(workspace.assumptions)
            requirements_draft.open_questions = list(workspace.open_questions)
            self._store.save_requirements_draft(requirements_draft)

        return workspace, requirements_draft

    def build_export_payload(self, workspace_id: str) -> dict[str, object]:
        workspace = self._require_workspace(workspace_id)
        context_pack = self.get_context_pack(workspace_id)
        requirements_draft = self.get_requirements_draft(workspace_id)
        backlog_draft = self.get_backlog_draft(workspace_id)
        workflow = self.get_workflow_state(workspace_id)
        return {
            "workspace": workspace.model_dump(mode="json"),
            "workflow": workflow.model_dump(mode="json"),
            "context_pack": context_pack.model_dump(mode="json") if context_pack else None,
            "requirements_draft": requirements_draft.model_dump(mode="json") if requirements_draft else None,
            "backlog_draft": backlog_draft.model_dump(mode="json") if backlog_draft else None,
            "export_notes": {
                "jira_publish_available": False,
                "message": "Direct Jira publish is intentionally deferred. Use these exports for review and handoff.",
            },
        }

    def _require_workspace(self, workspace_id: str) -> FeatureWorkspace:
        workspace = self.get_workspace(workspace_id)
        if workspace is None:
            raise ValueError(f"Workspace '{workspace_id}' not found.")
        return workspace

    def _build_evidence_item(
        self,
        ref_id: str,
        rationale: str | None = None,
        title_override: str | None = None,
    ) -> WorkspaceEvidenceItem:
        chunk = self._knowledge.get_chunk(ref_id)
        if isinstance(chunk, ChunkRecord):
            artifact = self._knowledge.get_artifact(chunk.artifact_id)
            if artifact is None:
                raise ValueError(f"Chunk '{ref_id}' is missing its parent artifact.")
            derived_title = title_override.strip() if title_override and title_override.strip() else (
                f"{artifact.metadata.title} / chunk {chunk.chunk_index}"
            )
            return WorkspaceEvidenceItem(
                evidence_id=self._evidence_id(WorkspaceEvidenceType.CHUNK, ref_id),
                evidence_type=WorkspaceEvidenceType.CHUNK,
                ref_id=ref_id,
                title=derived_title,
                source_system=artifact.metadata.source_system.value,
                artifact_kind=artifact.metadata.artifact_kind.value,
                rationale=rationale,
                metadata={
                    "artifact_id": artifact.metadata.artifact_id,
                    "chunk_index": chunk.chunk_index,
                    "snippet": chunk.text[:240],
                },
            )

        artifact = self._knowledge.get_artifact(ref_id)
        if isinstance(artifact, ArtifactRecord):
            derived_title = title_override.strip() if title_override and title_override.strip() else artifact.metadata.title
            return WorkspaceEvidenceItem(
                evidence_id=self._evidence_id(WorkspaceEvidenceType.ARTIFACT, ref_id),
                evidence_type=WorkspaceEvidenceType.ARTIFACT,
                ref_id=ref_id,
                title=derived_title,
                source_system=artifact.metadata.source_system.value,
                artifact_kind=artifact.metadata.artifact_kind.value,
                rationale=rationale,
                metadata={"project_key": artifact.metadata.project_key},
            )

        raise ValueError(f"Ref '{ref_id}' was not found as an artifact or chunk.")

    def _evidence_id(self, evidence_type: WorkspaceEvidenceType, ref_id: str) -> str:
        digest = hashlib.sha1(f"{evidence_type.value}|{ref_id}".encode("utf-8")).hexdigest()[:12]
        return f"evidence-{digest}"

    def _summarize_request(self, request_text: str) -> str | None:
        compact = " ".join(request_text.split())
        if not compact:
            return None
        if len(compact) > 180:
            return compact[:177] + "..."
        return compact

    def _split_lines(self, value: str) -> list[str]:
        return [line.strip() for line in value.splitlines() if line.strip()]


@lru_cache
def get_requirements_service() -> RequirementsService:
    return RequirementsService()
