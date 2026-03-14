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
    ContextRelatedHit,
    ContextSearchHit,
    FeatureWorkspace,
    RequirementsDraft,
    WorkspaceEvidenceItem,
    WorkspaceEvidenceType,
    WorkspaceStatus,
)
from backend.app.services.requirements.requirements_generator import RequirementsGenerator
from backend.app.services.requirements.state_models import (
    ContextSnapshot,
    GenerationHistoryEntry,
    GenerationType,
    ReviewNote,
    ReviewNoteType,
    SnapshotRelatedHit,
    SnapshotSearchHit,
    StageStatusRecord,
    StageStatusValue,
    ValidationResult,
    ValidationTargetType,
    WorkspaceOperationalState,
    WorkspaceStage,
)
from backend.app.services.requirements.validator import get_workspace_validator
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
        self.save_workspace_state(
            WorkspaceOperationalState(
                workspace_id=workspace.workspace_id,
                current_stage=WorkspaceStage.CREATE_WORKSPACE,
                stage_statuses=[],
                last_action="workspace_created",
                last_updated_at=now,
            )
        )
        self._sync_workspace_state(workspace.workspace_id, last_action="workspace_created")
        return workspace

    def list_workspaces(self) -> list[FeatureWorkspace]:
        return self._store.list_workspaces()

    def get_workspace(self, workspace_id: str) -> FeatureWorkspace | None:
        return self._store.get_workspace(workspace_id)

    def get_context_pack(self, workspace_id: str) -> ContextPack | None:
        return self._store.get_context_pack(workspace_id)

    def get_workspace_state(self, workspace_id: str) -> WorkspaceOperationalState | None:
        return self._store.get_state(workspace_id)

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
        snapshot = self.create_context_snapshot(workspace_id, context_pack)
        workspace.generated_context_summary = context_pack.summary_text
        if workspace.status == WorkspaceStatus.DRAFT:
            workspace.status = WorkspaceStatus.READY_FOR_REQUIREMENTS
        workspace.updated_at = datetime.now(timezone.utc)
        self._store.save_workspace(workspace)
        self._record_generation_history(
            workspace_id,
            generation_type=GenerationType.CONTEXT_PACK,
            input_refs=list(dict.fromkeys([item.ref_id for item in workspace.pinned_evidence])),
            output_ref=snapshot.snapshot_id,
            notes="Context snapshot created from current lexical and related-artifact signals.",
        )
        self._sync_workspace_state(
            workspace_id,
            last_action="context_built",
            latest_context_snapshot_id=snapshot.snapshot_id,
            bump_generation=True,
        )
        self.run_workspace_validation(
            workspace_id,
            target_type=ValidationTargetType.CONTEXT_SNAPSHOT,
            target_id=snapshot.snapshot_id,
        )
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
        self._sync_workspace_state(workspace_id, last_action="evidence_pinned")
        return workspace

    def unpin_evidence(self, workspace_id: str, evidence_id: str) -> FeatureWorkspace:
        workspace = self._require_workspace(workspace_id)
        workspace.pinned_evidence = [
            item for item in workspace.pinned_evidence if item.evidence_id != evidence_id
        ]
        workspace.updated_at = datetime.now(timezone.utc)
        self._store.save_workspace(workspace)
        self._sync_workspace_state(workspace_id, last_action="evidence_unpinned")
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
        self._record_generation_history(
            workspace_id,
            generation_type=GenerationType.REQUIREMENTS,
            input_refs=list(context_pack.selected_source_refs),
            output_ref=draft.draft_id,
            model_name=self._settings.requirements_generation_model_name or self._settings.llm_model_name,
            notes="Requirements draft generated from current context and pinned evidence.",
        )
        self._sync_workspace_state(
            workspace_id,
            last_action="requirements_generated",
            latest_requirements_draft_id=draft.draft_id,
            bump_generation=True,
        )
        self.run_workspace_validation(
            workspace_id,
            target_type=ValidationTargetType.REQUIREMENTS_DRAFT,
            target_id=draft.draft_id,
        )
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
        self._sync_workspace_state(
            workspace_id,
            last_action="requirements_saved",
            latest_requirements_draft_id=draft.draft_id,
        )
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
        self._record_generation_history(
            workspace_id,
            generation_type=GenerationType.BACKLOG,
            input_refs=[requirements_draft.draft_id],
            output_ref=draft.backlog_id,
            model_name=self._settings.requirements_generation_model_name or self._settings.llm_model_name,
            notes=f"Backlog draft generated in {draft.split_mode} mode.",
        )
        self._sync_workspace_state(
            workspace_id,
            last_action="backlog_generated",
            latest_backlog_draft_id=draft.backlog_id,
            bump_generation=True,
        )
        self.run_workspace_validation(
            workspace_id,
            target_type=ValidationTargetType.BACKLOG_DRAFT,
            target_id=draft.backlog_id,
        )
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
        self._sync_workspace_state(
            workspace_id,
            last_action="backlog_saved",
            latest_backlog_draft_id=draft.backlog_id,
        )
        return draft

    def get_requirements_draft(self, workspace_id: str) -> RequirementsDraft | None:
        return self._store.get_requirements_draft(workspace_id)

    def get_backlog_draft(self, workspace_id: str) -> BacklogDraft | None:
        return self._store.get_backlog_draft(workspace_id)

    def save_workspace_state(self, state: WorkspaceOperationalState) -> None:
        self._store.save_state(state)

    def create_context_snapshot(self, workspace_id: str, context_pack: ContextPack) -> ContextSnapshot:
        snapshot = ContextSnapshot(
            snapshot_id=f"snapshot-{uuid.uuid4().hex}",
            workspace_id=workspace_id,
            created_at=datetime.now(timezone.utc),
            query_text=context_pack.query_text,
            summary_text=context_pack.summary_text,
            selected_source_refs=list(context_pack.selected_source_refs),
            search_hits=[self._snapshot_search_hit(item) for item in context_pack.search_hits],
            related_hits=[self._snapshot_related_hit(item) for item in context_pack.related_hits],
            pinned_items=[item.ref_id for item in context_pack.pinned_items],
            warnings=list(context_pack.warnings),
        )
        self._store.save_context_snapshot(snapshot)
        return snapshot

    def list_context_snapshots(self, workspace_id: str) -> list[ContextSnapshot]:
        self._require_workspace(workspace_id)
        return self._store.list_context_snapshots(workspace_id)

    def get_context_snapshot(self, workspace_id: str, snapshot_id: str) -> ContextSnapshot | None:
        self._require_workspace(workspace_id)
        return self._store.get_context_snapshot(workspace_id, snapshot_id)

    def add_review_note(
        self,
        workspace_id: str,
        *,
        title: str,
        body: str,
        note_type: ReviewNoteType = ReviewNoteType.ANALYST_NOTE,
        linked_refs: list[str] | None = None,
    ) -> ReviewNote:
        self._require_workspace(workspace_id)
        if not title.strip():
            raise ValueError("Review note title must not be empty.")
        if not body.strip():
            raise ValueError("Review note body must not be empty.")
        note = ReviewNote(
            review_note_id=f"review-{uuid.uuid4().hex}",
            workspace_id=workspace_id,
            created_at=datetime.now(timezone.utc),
            note_type=note_type,
            title=title.strip(),
            body=body.strip(),
            linked_refs=list(dict.fromkeys(linked_refs or [])),
        )
        self._store.save_review_note(note)
        self._sync_workspace_state(
            workspace_id,
            last_action="review_note_added",
            latest_review_note_id=note.review_note_id,
        )
        return note

    def list_review_notes(self, workspace_id: str) -> list[ReviewNote]:
        self._require_workspace(workspace_id)
        return self._store.list_review_notes(workspace_id)

    def run_workspace_validation(
        self,
        workspace_id: str,
        *,
        target_type: ValidationTargetType = ValidationTargetType.WORKSPACE,
        target_id: str | None = None,
    ) -> ValidationResult:
        workspace = self._require_workspace(workspace_id)
        latest_snapshot = self._latest_context_snapshot(workspace_id)
        result = get_workspace_validator().validate(
            workspace,
            context_snapshot=latest_snapshot,
            requirements_draft=self.get_requirements_draft(workspace_id),
            backlog_draft=self.get_backlog_draft(workspace_id),
            target_type=target_type,
            target_id=target_id,
        )
        self._store.save_validation_result(result)
        self._record_generation_history(
            workspace_id,
            generation_type=GenerationType.VALIDATION,
            input_refs=[ref for ref in [latest_snapshot.snapshot_id if latest_snapshot else None, target_id] if ref],
            output_ref=result.validation_result_id,
            notes=result.summary,
        )
        self._sync_workspace_state(
            workspace_id,
            last_action="validation_run",
            latest_validation_result_id=result.validation_result_id,
            bump_generation=True,
        )
        return result

    def list_validation_results(self, workspace_id: str) -> list[ValidationResult]:
        self._require_workspace(workspace_id)
        return self._store.list_validation_results(workspace_id)

    def list_generation_history(self, workspace_id: str) -> list[GenerationHistoryEntry]:
        self._require_workspace(workspace_id)
        return self._store.list_generation_history(workspace_id)

    def get_recommended_next_action(self, workspace_id: str) -> dict[str, object]:
        workflow = self.get_workflow_state(workspace_id)
        return {
            "current_stage": workflow.current_stage,
            "next_stage": workflow.next_stage,
            "recommended_next_action": workflow.recommended_next_action,
            "blocking_items": list(workflow.blocking_items),
            "warnings": list(workflow.warnings),
        }

    def get_workspace_dashboard_data(self, workspace_id: str) -> dict[str, object]:
        workspace = self._require_workspace(workspace_id)
        state = self._sync_workspace_state(workspace_id)
        context_snapshots = self.list_context_snapshots(workspace_id)
        review_notes = self.list_review_notes(workspace_id)
        validations = self.list_validation_results(workspace_id)
        history = self.list_generation_history(workspace_id)
        requirements_draft = self.get_requirements_draft(workspace_id)
        backlog_draft = self.get_backlog_draft(workspace_id)
        workflow = self.get_workflow_state(workspace_id)
        latest_snapshot = context_snapshots[0] if context_snapshots else None
        latest_validation = validations[0] if validations else None
        return {
            "workspace": workspace,
            "state": state,
            "workflow": workflow,
            "recommended_next_action": self.get_recommended_next_action(workspace_id),
            "context_snapshots": context_snapshots,
            "latest_context_snapshot": latest_snapshot,
            "review_notes": review_notes,
            "validation_results": validations,
            "latest_validation_result": latest_validation,
            "generation_history": history,
            "requirements_draft": requirements_draft,
            "backlog_draft": backlog_draft,
            "counts": {
                "pinned_evidence": len(workspace.pinned_evidence),
                "context_snapshots": len(context_snapshots),
                "review_notes": len(review_notes),
                "validation_issues": len(latest_validation.issues) if latest_validation else 0,
                "requirements_items": len(requirements_draft.requirements) if requirements_draft else 0,
                "backlog_items": len(backlog_draft.items) if backlog_draft else 0,
            },
        }

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

        self._sync_workspace_state(workspace_id, last_action="review_fields_updated")
        return workspace, requirements_draft

    def build_export_payload(self, workspace_id: str) -> dict[str, object]:
        workspace = self._require_workspace(workspace_id)
        context_pack = self.get_context_pack(workspace_id)
        requirements_draft = self.get_requirements_draft(workspace_id)
        backlog_draft = self.get_backlog_draft(workspace_id)
        workflow = self.get_workflow_state(workspace_id)
        state = self._sync_workspace_state(workspace_id)
        latest_snapshot = self._latest_context_snapshot(workspace_id)
        latest_validation = self.list_validation_results(workspace_id)[:1]
        return {
            "workspace": workspace.model_dump(mode="json"),
            "operational_state": state.model_dump(mode="json"),
            "workflow": workflow.model_dump(mode="json"),
            "context_pack": context_pack.model_dump(mode="json") if context_pack else None,
            "latest_context_snapshot": latest_snapshot.model_dump(mode="json") if latest_snapshot else None,
            "requirements_draft": requirements_draft.model_dump(mode="json") if requirements_draft else None,
            "backlog_draft": backlog_draft.model_dump(mode="json") if backlog_draft else None,
            "latest_validation_result": latest_validation[0].model_dump(mode="json") if latest_validation else None,
            "generation_history": [item.model_dump(mode="json") for item in self.list_generation_history(workspace_id)[:10]],
            "review_notes": [item.model_dump(mode="json") for item in self.list_review_notes(workspace_id)[:10]],
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

    def _record_generation_history(
        self,
        workspace_id: str,
        *,
        generation_type: GenerationType,
        input_refs: list[str],
        output_ref: str | None,
        model_name: str | None = None,
        notes: str | None = None,
    ) -> GenerationHistoryEntry:
        entry = GenerationHistoryEntry(
            history_entry_id=f"history-{uuid.uuid4().hex}",
            workspace_id=workspace_id,
            created_at=datetime.now(timezone.utc),
            generation_type=generation_type,
            input_refs=list(dict.fromkeys(input_refs)),
            output_ref=output_ref,
            model_name=model_name,
            notes=notes,
        )
        self._store.save_generation_history(entry)
        return entry

    def _sync_workspace_state(
        self,
        workspace_id: str,
        *,
        last_action: str | None = None,
        latest_context_snapshot_id: str | None = None,
        latest_requirements_draft_id: str | None = None,
        latest_backlog_draft_id: str | None = None,
        latest_validation_result_id: str | None = None,
        latest_review_note_id: str | None = None,
        bump_generation: bool = False,
    ) -> WorkspaceOperationalState:
        workspace = self._require_workspace(workspace_id)
        state = self._store.get_state(workspace_id)
        if state is None:
            state = WorkspaceOperationalState(
                workspace_id=workspace_id,
                current_stage=WorkspaceStage.CREATE_WORKSPACE,
                last_updated_at=datetime.now(timezone.utc),
            )
        latest_snapshot = self._latest_context_snapshot(workspace_id)
        latest_requirements_draft = self.get_requirements_draft(workspace_id)
        latest_backlog_draft = self.get_backlog_draft(workspace_id)
        latest_validation_results = self.list_validation_results(workspace_id)
        latest_review_notes = self.list_review_notes(workspace_id)

        workflow = self.get_workflow_state(workspace_id)
        stage_statuses: list[StageStatusRecord] = []
        blocking_stage = workflow.next_stage if workflow.blocking_items else None
        for item in workflow.stage_statuses:
            mapped_stage = WorkspaceStage(item.stage)
            mapped_status = StageStatusValue(item.status)
            if blocking_stage and item.stage == blocking_stage and workflow.blocking_items:
                mapped_status = StageStatusValue.BLOCKED
            stage_statuses.append(
                StageStatusRecord(
                    stage=mapped_stage,
                    label=item.label,
                    status=mapped_status,
                    detail=item.detail,
                )
            )

        state.current_stage = WorkspaceStage(workflow.current_stage)
        state.stage_statuses = stage_statuses
        state.pinned_evidence_ids = [item.evidence_id for item in workspace.pinned_evidence]
        state.latest_context_snapshot_id = (
            latest_context_snapshot_id
            or (latest_snapshot.snapshot_id if latest_snapshot else None)
            or state.latest_context_snapshot_id
        )
        state.latest_requirements_draft_id = (
            latest_requirements_draft_id
            or (latest_requirements_draft.draft_id if latest_requirements_draft else None)
            or state.latest_requirements_draft_id
        )
        state.latest_backlog_draft_id = (
            latest_backlog_draft_id
            or (latest_backlog_draft.backlog_id if latest_backlog_draft else None)
            or state.latest_backlog_draft_id
        )
        state.latest_validation_result_id = (
            latest_validation_result_id
            or (latest_validation_results[0].validation_result_id if latest_validation_results else None)
            or state.latest_validation_result_id
        )
        state.latest_review_note_id = (
            latest_review_note_id
            or (latest_review_notes[0].review_note_id if latest_review_notes else None)
            or state.latest_review_note_id
        )
        state.last_action = last_action or state.last_action
        state.warnings = list(workflow.warnings)
        state.blockers = list(workflow.blocking_items)
        state.last_updated_at = datetime.now(timezone.utc)
        if bump_generation:
            state.generation_count += 1
        self._store.save_state(state)
        return state

    def _latest_context_snapshot(self, workspace_id: str) -> ContextSnapshot | None:
        snapshots = self.list_context_snapshots(workspace_id)
        return snapshots[0] if snapshots else None

    def _snapshot_search_hit(self, hit: ContextSearchHit) -> SnapshotSearchHit:
        return SnapshotSearchHit(
            ref_id=hit.ref_id,
            artifact_id=hit.artifact_id,
            title=hit.title,
            source_system=hit.source_system,
            artifact_kind=hit.artifact_kind,
            snippet=hit.snippet,
            score=hit.score,
        )

    def _snapshot_related_hit(self, hit: ContextRelatedHit) -> SnapshotRelatedHit:
        return SnapshotRelatedHit(
            ref_id=hit.ref_id,
            title=hit.title,
            source_system=hit.source_system,
            artifact_kind=hit.artifact_kind,
            edge_types=list(hit.edge_types),
            score=hit.score,
            rationale=hit.rationale,
        )


@lru_cache
def get_requirements_service() -> RequirementsService:
    return RequirementsService()
