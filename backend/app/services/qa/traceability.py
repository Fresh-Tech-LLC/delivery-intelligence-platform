"""Deterministic requirement-to-test traceability for QA Studio."""
from __future__ import annotations

import hashlib

from backend.app.services.qa.models import TraceabilityLink, TraceabilityLinkType
from backend.app.services.requirements.models import BacklogDraft, ContextPack, FeatureWorkspace, RequirementsDraft
from backend.app.services.requirements.requirements_service import RequirementsService


def build_traceability_for_workspace(
    requirements_service: RequirementsService,
    workspace_id: str,
) -> list[TraceabilityLink]:
    """Build deterministic traceability links from a Requirements Studio workspace."""
    workspace = requirements_service.get_workspace(workspace_id)
    if workspace is None:
        raise ValueError(f"Workspace '{workspace_id}' not found.")
    requirements_draft = requirements_service.get_requirements_draft(workspace_id)
    backlog_draft = requirements_service.get_backlog_draft(workspace_id)
    context_pack = requirements_service.get_context_pack(workspace_id)

    links: list[TraceabilityLink] = []
    seen: set[str] = set()

    def add(link_type: TraceabilityLinkType, ref_id: str, title: str | None = None, metadata: dict | None = None) -> None:
        link_id = _link_id(link_type, ref_id)
        if link_id in seen:
            return
        seen.add(link_id)
        links.append(
            TraceabilityLink(
                link_id=link_id,
                link_type=link_type,
                ref_id=ref_id,
                title=title,
                metadata=metadata or {},
            )
        )

    _add_requirement_links(add, requirements_draft)
    _add_backlog_links(add, backlog_draft)
    _add_workspace_links(add, workspace)
    _add_context_links(add, context_pack)
    return sorted(links, key=lambda item: (item.link_type.value, item.ref_id))


def _add_requirement_links(add, draft: RequirementsDraft | None) -> None:
    if draft is None:
        return
    for requirement in draft.requirements:
        add(
            TraceabilityLinkType.REQUIREMENT,
            requirement.requirement_id,
            title=requirement.title,
            metadata={"priority": requirement.priority.value},
        )
        for index, ac in enumerate(requirement.acceptance_criteria, start=1):
            add(
                TraceabilityLinkType.ACCEPTANCE_CRITERIA,
                f"{requirement.requirement_id}#AC-{index:02d}",
                title=ac,
                metadata={"requirement_id": requirement.requirement_id},
            )
        for ref in requirement.source_refs:
            add(_classify_ref(ref), ref, metadata={"source": "requirement"})


def _add_backlog_links(add, draft: BacklogDraft | None) -> None:
    if draft is None:
        return
    for item in draft.items:
        add(
            TraceabilityLinkType.BACKLOG_ITEM,
            item.item_id,
            title=item.title,
            metadata={"item_type": item.item_type.value},
        )
        for ref in item.source_refs:
            add(_classify_ref(ref), ref, metadata={"source": "backlog"})


def _add_workspace_links(add, workspace: FeatureWorkspace) -> None:
    for evidence in workspace.pinned_evidence:
        add(
            _classify_ref(evidence.ref_id),
            evidence.ref_id,
            title=evidence.title,
            metadata={"source": "pinned_evidence", "evidence_type": evidence.evidence_type.value},
        )


def _add_context_links(add, context_pack: ContextPack | None) -> None:
    if context_pack is None:
        return
    for hit in context_pack.related_hits:
        add(
            _classify_ref(hit.ref_id),
            hit.ref_id,
            title=hit.title,
            metadata={"source": "related_hit", "edge_types": hit.edge_types},
        )


def _classify_ref(ref_id: str) -> TraceabilityLinkType:
    if ref_id.startswith("appian-"):
        return TraceabilityLinkType.APPIAN_OBJECT
    if "-chunk-" in ref_id:
        return TraceabilityLinkType.CHUNK
    return TraceabilityLinkType.ARTIFACT


def _link_id(link_type: TraceabilityLinkType, ref_id: str) -> str:
    digest = hashlib.sha1(f"{link_type.value}|{ref_id}".encode("utf-8")).hexdigest()[:12]
    return f"trace-{digest}"
