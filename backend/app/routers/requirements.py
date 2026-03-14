from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from backend.app.schemas_requirements import (
    CreateReviewNoteRequest,
    CreateWorkspaceRequest,
    GenerateBacklogRequest,
    PinEvidenceRequest,
    RunValidationRequest,
    ReviewUpdateRequest,
    UnpinEvidenceRequest,
)
from backend.app.services.llm_client import LLMError
from backend.app.services.requirements.state_models import ReviewNoteType, ValidationTargetType
from backend.app.services.requirements.requirements_service import (
    RequirementsService,
    get_requirements_service,
)

logger = logging.getLogger(__name__)

_BASE = Path(__file__).resolve().parents[3]
templates = Jinja2Templates(directory=str(_BASE / "frontend" / "templates"))

router = APIRouter(tags=["requirements"])


def _svc() -> RequirementsService:
    return get_requirements_service()


def _handle_error(exc: Exception) -> HTTPException:
    if isinstance(exc, LLMError):
        return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    logger.exception("Unexpected requirements router error")
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal error")


def _status_code_for_exc(exc: Exception) -> int:
    if isinstance(exc, LLMError):
        return status.HTTP_502_BAD_GATEWAY
    if isinstance(exc, ValueError):
        return status.HTTP_400_BAD_REQUEST
    return status.HTTP_500_INTERNAL_SERVER_ERROR


def _json_download(filename: str, payload: str) -> Response:
    return Response(
        content=payload,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _workspace_list_item(service: RequirementsService, workspace_id: str) -> dict[str, object]:
    workspace = service.get_workspace(workspace_id)
    if workspace is None:
        raise ValueError(f"Workspace '{workspace_id}' not found.")
    workflow = service.get_workflow_state(workspace_id)
    return {"workspace": workspace, "workflow": workflow}


def _group_backlog_items(backlog_draft) -> list[tuple[str, list[object]]]:
    if backlog_draft is None:
        return []
    grouped: dict[str, list[object]] = {}
    for item in backlog_draft.items:
        grouped.setdefault(item.item_type.value, []).append(item)
    order = ["epic", "feature", "story", "task"]
    return [(label, grouped[label]) for label in order if label in grouped]


def _workspace_context(
    request: Request,
    workspace_id: str,
    *,
    panel: str = "intake",
    snapshot_id: str | None = None,
    result: str | None = None,
    error: str | None = None,
) -> dict[str, object]:
    service = _svc()
    workspace = service.get_workspace(workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail=f"Workspace '{workspace_id}' not found.")
    dashboard = service.get_workspace_dashboard_data(workspace_id)
    context_pack = service.get_context_pack(workspace_id)
    requirements_draft = dashboard["requirements_draft"]
    backlog_draft = dashboard["backlog_draft"]
    workflow = dashboard["workflow"]
    export_payload = service.build_export_payload(workspace_id)
    context_snapshots = dashboard["context_snapshots"]
    selected_snapshot = (
        service.get_context_snapshot(workspace_id, snapshot_id)
        if snapshot_id else dashboard["latest_context_snapshot"]
    )
    review_notes = dashboard["review_notes"]
    validations = dashboard["validation_results"]
    latest_validation = dashboard["latest_validation_result"]
    generation_history = dashboard["generation_history"]
    state = dashboard["state"]
    active_panel = panel if panel in {"intake", "context", "evidence", "requirements", "review", "backlog", "export"} else "intake"
    return {
        "request": request,
        "session_id": None,
        "workspace": workspace,
        "dashboard": dashboard,
        "state": state,
        "context_pack": context_pack,
        "context_snapshots": context_snapshots,
        "selected_snapshot": selected_snapshot,
        "review_notes": review_notes,
        "validation_results": validations,
        "latest_validation_result": latest_validation,
        "generation_history": generation_history,
        "requirements_draft": requirements_draft,
        "backlog_draft": backlog_draft,
        "workflow": workflow,
        "backlog_groups": _group_backlog_items(backlog_draft),
        "active_panel": active_panel,
        "example_ref_ids": [
            hit.ref_id for hit in (context_pack.search_hits[:3] if context_pack else [])
        ] or [
            hit.ref_id for hit in (context_pack.related_hits[:3] if context_pack else [])
        ],
        "export_payload_json": json_dumps(export_payload),
        "context_pack_json": context_pack.model_dump_json(indent=2) if context_pack else "",
        "requirements_json": requirements_draft.model_dump_json(indent=2) if requirements_draft else "",
        "backlog_json": backlog_draft.model_dump_json(indent=2) if backlog_draft else "",
        "workspace_export_json": workspace.model_dump_json(indent=2),
        "requirements_export_json": requirements_draft.model_dump_json(indent=2) if requirements_draft else "",
        "backlog_export_json": backlog_draft.model_dump_json(indent=2) if backlog_draft else "",
        "combined_export_json": json_dumps(export_payload),
        "result": result,
        "error": error,
    }


@router.get("/requirements", response_class=HTMLResponse)
async def requirements_index(request: Request):
    service = _svc()
    workspace_rows = [
        {
            **_workspace_list_item(service, workspace.workspace_id),
            "dashboard": service.get_workspace_dashboard_data(workspace.workspace_id),
        }
        for workspace in service.list_workspaces()
    ]
    return templates.TemplateResponse(
        "requirements_index.html",
        {
            "request": request,
            "session_id": None,
            "workspace_rows": workspace_rows,
            "error": None,
        },
    )


@router.post("/requirements/workspaces")
async def create_workspace_page(
    title: str = Form(...),
    request_text: str = Form(...),
    project_key: str = Form(""),
):
    try:
        workspace = _svc().create_workspace(title=title, request_text=request_text, project_key=project_key or None)
    except Exception as exc:
        raise _handle_error(exc) from exc
    return RedirectResponse(url=f"/requirements/workspaces/{workspace.workspace_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/requirements/workspaces/{workspace_id}", response_class=HTMLResponse)
async def workspace_page(request: Request, workspace_id: str, panel: str = "intake", snapshot_id: str | None = None):
    return templates.TemplateResponse(
        "requirements_workspace.html",
        _workspace_context(request, workspace_id, panel=panel, snapshot_id=snapshot_id),
    )


@router.get("/requirements/workspaces/{workspace_id}/dashboard", response_class=HTMLResponse)
async def workspace_dashboard_page(request: Request, workspace_id: str, panel: str = "intake"):
    return templates.TemplateResponse(
        "requirements_workspace.html",
        _workspace_context(request, workspace_id, panel=panel),
    )


@router.post("/requirements/workspaces/{workspace_id}/context-pack", response_class=HTMLResponse)
async def build_context_pack_page(request: Request, workspace_id: str):
    try:
        _svc().build_context_pack(workspace_id)
        return templates.TemplateResponse(
            "requirements_workspace.html",
            _workspace_context(request, workspace_id, panel="context", result="Context pack rebuilt."),
        )
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        return templates.TemplateResponse(
            "requirements_workspace.html",
            _workspace_context(request, workspace_id, panel="context", error=str(exc)),
            status_code=_status_code_for_exc(exc),
        )


@router.post("/requirements/workspaces/{workspace_id}/pin", response_class=HTMLResponse)
async def pin_evidence_page(
    request: Request,
    workspace_id: str,
    ref_id: str = Form(...),
    title: str = Form(""),
    rationale: str = Form(""),
):
    try:
        _svc().pin_evidence(
            workspace_id,
            ref_id=ref_id,
            rationale=rationale or None,
            title=title or None,
        )
        return templates.TemplateResponse(
            "requirements_workspace.html",
            _workspace_context(request, workspace_id, panel="evidence", result=f"Pinned evidence {ref_id}."),
        )
    except Exception as exc:
        return templates.TemplateResponse(
            "requirements_workspace.html",
            _workspace_context(request, workspace_id, panel="evidence", error=str(exc)),
            status_code=_status_code_for_exc(exc),
        )


@router.post("/requirements/workspaces/{workspace_id}/unpin", response_class=HTMLResponse)
async def unpin_evidence_page(
    request: Request,
    workspace_id: str,
    evidence_id: str = Form(...),
):
    try:
        _svc().unpin_evidence(workspace_id, evidence_id=evidence_id)
        return templates.TemplateResponse(
            "requirements_workspace.html",
            _workspace_context(request, workspace_id, panel="evidence", result="Pinned evidence removed."),
        )
    except Exception as exc:
        return templates.TemplateResponse(
            "requirements_workspace.html",
            _workspace_context(request, workspace_id, panel="evidence", error=str(exc)),
            status_code=_status_code_for_exc(exc),
        )


@router.post("/requirements/workspaces/{workspace_id}/generate-requirements", response_class=HTMLResponse)
async def generate_requirements_page(request: Request, workspace_id: str):
    try:
        _svc().generate_requirements(workspace_id)
        return templates.TemplateResponse(
            "requirements_workspace.html",
            _workspace_context(request, workspace_id, panel="requirements", result="Requirements draft generated."),
        )
    except Exception as exc:
        return templates.TemplateResponse(
            "requirements_workspace.html",
            _workspace_context(request, workspace_id, panel="requirements", error=str(exc)),
            status_code=_status_code_for_exc(exc),
        )


@router.post("/requirements/workspaces/{workspace_id}/generate-backlog", response_class=HTMLResponse)
async def generate_backlog_page(
    request: Request,
    workspace_id: str,
    split_mode: str = Form(""),
):
    try:
        _svc().generate_backlog(workspace_id, split_mode=split_mode or None)
        return templates.TemplateResponse(
            "requirements_workspace.html",
            _workspace_context(request, workspace_id, panel="backlog", result="Backlog draft generated."),
        )
    except Exception as exc:
        return templates.TemplateResponse(
            "requirements_workspace.html",
            _workspace_context(request, workspace_id, panel="backlog", error=str(exc)),
            status_code=_status_code_for_exc(exc),
        )


@router.post("/requirements/workspaces/{workspace_id}/requirements/save", response_class=HTMLResponse)
async def save_requirements_page(
    request: Request,
    workspace_id: str,
    requirements_json: str = Form(...),
):
    try:
        _svc().save_requirements_draft(workspace_id, requirements_json)
        return templates.TemplateResponse(
            "requirements_workspace.html",
            _workspace_context(request, workspace_id, panel="requirements", result="Requirements draft saved."),
        )
    except Exception as exc:
        return templates.TemplateResponse(
            "requirements_workspace.html",
            _workspace_context(request, workspace_id, panel="requirements", error=str(exc)),
            status_code=_status_code_for_exc(exc),
        )


@router.post("/requirements/workspaces/{workspace_id}/backlog/save", response_class=HTMLResponse)
async def save_backlog_page(
    request: Request,
    workspace_id: str,
    backlog_json: str = Form(...),
):
    try:
        _svc().save_backlog_draft(workspace_id, backlog_json)
        return templates.TemplateResponse(
            "requirements_workspace.html",
            _workspace_context(request, workspace_id, panel="backlog", result="Backlog draft saved."),
        )
    except Exception as exc:
        return templates.TemplateResponse(
            "requirements_workspace.html",
            _workspace_context(request, workspace_id, panel="backlog", error=str(exc)),
            status_code=_status_code_for_exc(exc),
        )


@router.post("/requirements/workspaces/{workspace_id}/review", response_class=HTMLResponse)
async def update_review_page(
    request: Request,
    workspace_id: str,
    assumptions_text: str = Form(""),
    open_questions_text: str = Form(""),
    problem_statement: str = Form(""),
    business_outcome: str = Form(""),
    requirements_generation_notes: str = Form(""),
):
    try:
        _svc().update_review_fields(
            workspace_id,
            assumptions_text=assumptions_text,
            open_questions_text=open_questions_text,
            problem_statement=problem_statement,
            business_outcome=business_outcome,
            requirements_generation_notes=requirements_generation_notes,
        )
        return templates.TemplateResponse(
            "requirements_workspace.html",
            _workspace_context(request, workspace_id, panel="review", result="Review fields updated."),
        )
    except Exception as exc:
        return templates.TemplateResponse(
            "requirements_workspace.html",
            _workspace_context(request, workspace_id, panel="review", error=str(exc)),
            status_code=_status_code_for_exc(exc),
        )


@router.post("/requirements/workspaces/{workspace_id}/review-notes", response_class=HTMLResponse)
async def add_review_note_page(
    request: Request,
    workspace_id: str,
    note_type: str = Form("analyst_note"),
    title: str = Form(...),
    body: str = Form(...),
    linked_refs: str = Form(""),
):
    try:
        _svc().add_review_note(
            workspace_id,
            title=title,
            body=body,
            note_type=ReviewNoteType(note_type),
            linked_refs=[value.strip() for value in linked_refs.splitlines() if value.strip()],
        )
        return templates.TemplateResponse(
            "requirements_workspace.html",
            _workspace_context(request, workspace_id, panel="review", result="Review note added."),
        )
    except Exception as exc:
        return templates.TemplateResponse(
            "requirements_workspace.html",
            _workspace_context(request, workspace_id, panel="review", error=str(exc)),
            status_code=_status_code_for_exc(exc),
        )


@router.post("/requirements/workspaces/{workspace_id}/validate", response_class=HTMLResponse)
async def run_validation_page(request: Request, workspace_id: str):
    try:
        _svc().run_workspace_validation(workspace_id)
        return templates.TemplateResponse(
            "requirements_workspace.html",
            _workspace_context(request, workspace_id, panel="review", result="Validation completed."),
        )
    except Exception as exc:
        return templates.TemplateResponse(
            "requirements_workspace.html",
            _workspace_context(request, workspace_id, panel="review", error=str(exc)),
            status_code=_status_code_for_exc(exc),
        )


@router.get("/requirements/workspaces/{workspace_id}/context-snapshots/{snapshot_id}", response_class=HTMLResponse)
async def view_context_snapshot_page(request: Request, workspace_id: str, snapshot_id: str):
    return templates.TemplateResponse(
        "requirements_workspace.html",
        _workspace_context(request, workspace_id, panel="context", snapshot_id=snapshot_id),
    )


@router.get("/requirements/workspaces/{workspace_id}/export", response_class=HTMLResponse)
async def export_workspace_page(request: Request, workspace_id: str):
    service = _svc()
    workspace = service.get_workspace(workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail=f"Workspace '{workspace_id}' not found.")
    payload = service.build_export_payload(workspace_id)
    return templates.TemplateResponse(
        "requirements_export.html",
        {
            "request": request,
            "session_id": None,
            "workspace": workspace,
            "workflow": service.get_workflow_state(workspace_id),
            "state": service.get_workspace_state(workspace_id),
            "export_payload": payload,
            "export_payload_json": json_dumps(payload),
        },
    )


@router.get("/requirements/workspaces/{workspace_id}/export/requirements.json")
async def export_requirements_json(workspace_id: str):
    draft = _svc().get_requirements_draft(workspace_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Requirements draft not found.")
    return _json_download(f"{workspace_id}-requirements.json", draft.model_dump_json(indent=2))


@router.get("/requirements/workspaces/{workspace_id}/export/backlog.json")
async def export_backlog_json(workspace_id: str):
    draft = _svc().get_backlog_draft(workspace_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Backlog draft not found.")
    return _json_download(f"{workspace_id}-backlog.json", draft.model_dump_json(indent=2))


@router.get("/requirements/workspaces/{workspace_id}/export/workspace.json")
async def export_workspace_json(workspace_id: str):
    payload = _svc().build_export_payload(workspace_id)
    return _json_download(f"{workspace_id}-workspace-export.json", json_dumps(payload))


@router.get("/api/requirements/workspaces")
async def list_workspaces_api():
    return [item.model_dump(mode="json") for item in _svc().list_workspaces()]


@router.get("/api/requirements/workspaces/{workspace_id}")
async def get_workspace_api(workspace_id: str):
    service = _svc()
    try:
        dashboard = service.get_workspace_dashboard_data(workspace_id)
        context_pack = service.get_context_pack(workspace_id)
        return {
            "workspace": dashboard["workspace"].model_dump(mode="json"),
            "state": dashboard["state"].model_dump(mode="json"),
            "context_pack": context_pack.model_dump(mode="json") if context_pack else None,
            "latest_context_snapshot": dashboard["latest_context_snapshot"].model_dump(mode="json") if dashboard["latest_context_snapshot"] else None,
            "requirements_draft": dashboard["requirements_draft"].model_dump(mode="json") if dashboard["requirements_draft"] else None,
            "backlog_draft": dashboard["backlog_draft"].model_dump(mode="json") if dashboard["backlog_draft"] else None,
            "workflow": dashboard["workflow"].model_dump(mode="json"),
        }
    except Exception as exc:
        raise _handle_error(exc) from exc


@router.post("/api/requirements/workspaces")
async def create_workspace_api(body: CreateWorkspaceRequest):
    try:
        return _svc().create_workspace(
            title=body.title,
            request_text=body.request_text,
            project_key=body.project_key,
        ).model_dump(mode="json")
    except Exception as exc:
        raise _handle_error(exc) from exc


@router.post("/api/requirements/workspaces/{workspace_id}/context-pack")
async def build_context_pack_api(workspace_id: str):
    try:
        return _svc().build_context_pack(workspace_id).model_dump(mode="json")
    except Exception as exc:
        raise _handle_error(exc) from exc


@router.post("/api/requirements/workspaces/{workspace_id}/pin")
async def pin_evidence_api(workspace_id: str, body: PinEvidenceRequest):
    try:
        return _svc().pin_evidence(
            workspace_id,
            body.ref_id,
            rationale=body.rationale,
            title=body.title,
        ).model_dump(mode="json")
    except Exception as exc:
        raise _handle_error(exc) from exc


@router.post("/api/requirements/workspaces/{workspace_id}/unpin")
async def unpin_evidence_api(workspace_id: str, body: UnpinEvidenceRequest):
    try:
        return _svc().unpin_evidence(workspace_id, body.evidence_id).model_dump(mode="json")
    except Exception as exc:
        raise _handle_error(exc) from exc


@router.post("/api/requirements/workspaces/{workspace_id}/generate-requirements")
async def generate_requirements_api(workspace_id: str):
    try:
        return _svc().generate_requirements(workspace_id).model_dump(mode="json")
    except Exception as exc:
        raise _handle_error(exc) from exc


@router.post("/api/requirements/workspaces/{workspace_id}/generate-backlog")
async def generate_backlog_api(workspace_id: str, body: GenerateBacklogRequest):
    try:
        return _svc().generate_backlog(workspace_id, split_mode=body.split_mode).model_dump(mode="json")
    except Exception as exc:
        raise _handle_error(exc) from exc


@router.post("/api/requirements/workspaces/{workspace_id}/review")
async def update_review_api(workspace_id: str, body: ReviewUpdateRequest):
    try:
        workspace, draft = _svc().update_review_fields(
            workspace_id,
            assumptions_text=body.assumptions_text,
            open_questions_text=body.open_questions_text,
            problem_statement=body.problem_statement,
            business_outcome=body.business_outcome,
            requirements_generation_notes=body.requirements_generation_notes,
        )
        return {
            "workspace": workspace.model_dump(mode="json"),
            "requirements_draft": draft.model_dump(mode="json") if draft else None,
            "workflow": _svc().get_workflow_state(workspace_id).model_dump(mode="json"),
        }
    except Exception as exc:
        raise _handle_error(exc) from exc


@router.get("/api/requirements/workspaces/{workspace_id}/export")
async def export_workspace_api(workspace_id: str):
    try:
        return _svc().build_export_payload(workspace_id)
    except Exception as exc:
        raise _handle_error(exc) from exc


@router.get("/api/requirements/workspaces/{workspace_id}/dashboard")
async def get_workspace_dashboard_api(workspace_id: str):
    try:
        dashboard = _svc().get_workspace_dashboard_data(workspace_id)
        return {
            "workspace": dashboard["workspace"].model_dump(mode="json"),
            "state": dashboard["state"].model_dump(mode="json"),
            "workflow": dashboard["workflow"].model_dump(mode="json"),
            "counts": dashboard["counts"],
            "recommended_next_action": dashboard["recommended_next_action"],
            "latest_context_snapshot": dashboard["latest_context_snapshot"].model_dump(mode="json") if dashboard["latest_context_snapshot"] else None,
            "latest_validation_result": dashboard["latest_validation_result"].model_dump(mode="json") if dashboard["latest_validation_result"] else None,
        }
    except Exception as exc:
        raise _handle_error(exc) from exc


@router.get("/api/requirements/workspaces/{workspace_id}/context-snapshots")
async def list_context_snapshots_api(workspace_id: str):
    try:
        return [item.model_dump(mode="json") for item in _svc().list_context_snapshots(workspace_id)]
    except Exception as exc:
        raise _handle_error(exc) from exc


@router.get("/api/requirements/workspaces/{workspace_id}/validation-results")
async def list_validation_results_api(workspace_id: str):
    try:
        return [item.model_dump(mode="json") for item in _svc().list_validation_results(workspace_id)]
    except Exception as exc:
        raise _handle_error(exc) from exc


@router.get("/api/requirements/workspaces/{workspace_id}/history")
async def list_generation_history_api(workspace_id: str):
    try:
        return [item.model_dump(mode="json") for item in _svc().list_generation_history(workspace_id)]
    except Exception as exc:
        raise _handle_error(exc) from exc


@router.get("/api/requirements/workspaces/{workspace_id}/review-notes")
async def list_review_notes_api(workspace_id: str):
    try:
        return [item.model_dump(mode="json") for item in _svc().list_review_notes(workspace_id)]
    except Exception as exc:
        raise _handle_error(exc) from exc


@router.post("/api/requirements/workspaces/{workspace_id}/review-notes")
async def add_review_note_api(workspace_id: str, body: CreateReviewNoteRequest):
    try:
        return _svc().add_review_note(
            workspace_id,
            title=body.title,
            body=body.body,
            note_type=body.note_type,
            linked_refs=body.linked_refs,
        ).model_dump(mode="json")
    except Exception as exc:
        raise _handle_error(exc) from exc


@router.post("/api/requirements/workspaces/{workspace_id}/validate")
async def run_validation_api(workspace_id: str, body: RunValidationRequest | None = None):
    try:
        target_type = ValidationTargetType(body.target_type) if body and body.target_type else ValidationTargetType.WORKSPACE
        target_id = body.target_id if body else None
        return _svc().run_workspace_validation(
            workspace_id,
            target_type=target_type,
            target_id=target_id,
        ).model_dump(mode="json")
    except Exception as exc:
        raise _handle_error(exc) from exc


def json_dumps(payload: object) -> str:
    import json

    return json.dumps(payload, indent=2, ensure_ascii=False)
