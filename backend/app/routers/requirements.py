from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from backend.app.schemas_requirements import CreateWorkspaceRequest, GenerateBacklogRequest, PinEvidenceRequest
from backend.app.services.llm_client import LLMError
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


def _workspace_context(request: Request, workspace_id: str, *, result: str | None = None, error: str | None = None) -> dict[str, object]:
    service = _svc()
    workspace = service.get_workspace(workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail=f"Workspace '{workspace_id}' not found.")
    context_pack = service.get_context_pack(workspace_id)
    requirements_draft = service.get_requirements_draft(workspace_id)
    backlog_draft = service.get_backlog_draft(workspace_id)
    return {
        "request": request,
        "session_id": None,
        "workspace": workspace,
        "context_pack": context_pack,
        "requirements_draft": requirements_draft,
        "backlog_draft": backlog_draft,
        "context_pack_json": context_pack.model_dump_json(indent=2) if context_pack else "",
        "requirements_json": requirements_draft.model_dump_json(indent=2) if requirements_draft else "",
        "backlog_json": backlog_draft.model_dump_json(indent=2) if backlog_draft else "",
        "result": result,
        "error": error,
    }


@router.get("/requirements", response_class=HTMLResponse)
async def requirements_index(request: Request):
    return templates.TemplateResponse(
        "requirements_index.html",
        {
            "request": request,
            "session_id": None,
            "workspaces": _svc().list_workspaces(),
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
async def workspace_page(request: Request, workspace_id: str):
    return templates.TemplateResponse("requirements_workspace.html", _workspace_context(request, workspace_id))


@router.post("/requirements/workspaces/{workspace_id}/context-pack", response_class=HTMLResponse)
async def build_context_pack_page(request: Request, workspace_id: str):
    try:
        _svc().build_context_pack(workspace_id)
        return templates.TemplateResponse(
            "requirements_workspace.html",
            _workspace_context(request, workspace_id, result="Context pack rebuilt."),
        )
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        return templates.TemplateResponse(
            "requirements_workspace.html",
            _workspace_context(request, workspace_id, error=str(exc)),
            status_code=_status_code_for_exc(exc),
        )


@router.post("/requirements/workspaces/{workspace_id}/pin", response_class=HTMLResponse)
async def pin_evidence_page(
    request: Request,
    workspace_id: str,
    ref_id: str = Form(...),
    rationale: str = Form(""),
):
    try:
        _svc().pin_evidence(workspace_id, ref_id=ref_id, rationale=rationale or None)
        return templates.TemplateResponse(
            "requirements_workspace.html",
            _workspace_context(request, workspace_id, result=f"Pinned evidence {ref_id}."),
        )
    except Exception as exc:
        return templates.TemplateResponse(
            "requirements_workspace.html",
            _workspace_context(request, workspace_id, error=str(exc)),
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
            _workspace_context(request, workspace_id, result="Pinned evidence removed."),
        )
    except Exception as exc:
        return templates.TemplateResponse(
            "requirements_workspace.html",
            _workspace_context(request, workspace_id, error=str(exc)),
            status_code=_status_code_for_exc(exc),
        )


@router.post("/requirements/workspaces/{workspace_id}/generate-requirements", response_class=HTMLResponse)
async def generate_requirements_page(request: Request, workspace_id: str):
    try:
        _svc().generate_requirements(workspace_id)
        return templates.TemplateResponse(
            "requirements_workspace.html",
            _workspace_context(request, workspace_id, result="Requirements draft generated."),
        )
    except Exception as exc:
        return templates.TemplateResponse(
            "requirements_workspace.html",
            _workspace_context(request, workspace_id, error=str(exc)),
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
            _workspace_context(request, workspace_id, result="Backlog draft generated."),
        )
    except Exception as exc:
        return templates.TemplateResponse(
            "requirements_workspace.html",
            _workspace_context(request, workspace_id, error=str(exc)),
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
            _workspace_context(request, workspace_id, result="Requirements draft saved."),
        )
    except Exception as exc:
        return templates.TemplateResponse(
            "requirements_workspace.html",
            _workspace_context(request, workspace_id, error=str(exc)),
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
            _workspace_context(request, workspace_id, result="Backlog draft saved."),
        )
    except Exception as exc:
        return templates.TemplateResponse(
            "requirements_workspace.html",
            _workspace_context(request, workspace_id, error=str(exc)),
            status_code=_status_code_for_exc(exc),
        )


@router.get("/api/requirements/workspaces")
async def list_workspaces_api():
    return [item.model_dump(mode="json") for item in _svc().list_workspaces()]


@router.get("/api/requirements/workspaces/{workspace_id}")
async def get_workspace_api(workspace_id: str):
    service = _svc()
    workspace = service.get_workspace(workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail=f"Workspace '{workspace_id}' not found.")
    context_pack = service.get_context_pack(workspace_id)
    requirements_draft = service.get_requirements_draft(workspace_id)
    backlog_draft = service.get_backlog_draft(workspace_id)
    return {
        "workspace": workspace.model_dump(mode="json"),
        "context_pack": context_pack.model_dump(mode="json") if context_pack else None,
        "requirements_draft": requirements_draft.model_dump(mode="json") if requirements_draft else None,
        "backlog_draft": backlog_draft.model_dump(mode="json") if backlog_draft else None,
    }


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
        return _svc().pin_evidence(workspace_id, body.ref_id, rationale=body.rationale).model_dump(mode="json")
    except Exception as exc:
        raise _handle_error(exc) from exc


@router.post("/api/requirements/workspaces/{workspace_id}/unpin")
async def unpin_evidence_api(workspace_id: str, evidence_id: str):
    try:
        return _svc().unpin_evidence(workspace_id, evidence_id).model_dump(mode="json")
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
