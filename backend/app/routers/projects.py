"""
Projects router — manage Jira project registry and per-project readiness checklists.
All endpoints under /api/projects.

Route ordering: /api/projects/default/* registered before /api/projects/{key}/*
so the literal string "default" is never captured by the {key} path parameter.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from backend.app.schemas import (
    AddProjectRequest,
    AddProjectResponse,
    ChecklistContentResponse,
    ChecklistHistoryResponse,
    ChecklistSaveRequest,
    ChecklistSaveResponse,
    ChecklistVersionContentResponse,
    DeleteChecklistResponse,
    ManagedProject,
    RemoveProjectResponse,
)
from backend.app.services.document_store import get_document_store, validate_project_key
from backend.app.services.jira_client import JiraError, get_jira_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _store():
    return get_document_store()


# ---------------------------------------------------------------------------
# Default checklist routes (must be registered before {key} routes)
# ---------------------------------------------------------------------------


@router.get("/default/checklist", response_model=ChecklistContentResponse)
def get_default_checklist():
    return _store().get_checklist_content_response(key=None)


@router.put("/default/checklist", response_model=ChecklistSaveResponse)
def save_default_checklist(body: ChecklistSaveRequest):
    try:
        return _store().save_checklist(key=None, content=body.content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/default/checklist/history", response_model=ChecklistHistoryResponse)
def get_default_checklist_history():
    return _store().list_checklist_versions(key=None)


@router.get("/default/checklist/history/{version}", response_model=ChecklistVersionContentResponse)
def get_default_checklist_version(version: int):
    try:
        return _store().load_checklist_version(key=None, version=version)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/default/checklist/restore/{version}", response_model=ChecklistSaveResponse)
def restore_default_checklist_version(version: int):
    try:
        return _store().restore_checklist_version(key=None, version=version)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Project registry
# ---------------------------------------------------------------------------


@router.get("/", response_model=list[ManagedProject])
def list_projects():
    return _store().load_project_registry()


@router.post("/", response_model=AddProjectResponse)
def add_project(body: AddProjectRequest):
    try:
        key = validate_project_key(body.jira_project_key.strip().upper())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Best-effort: try to fetch project name from Jira
    project_name = ""
    jira = get_jira_client()
    if jira.is_configured():
        try:
            info = jira.get_project(key)
            project_name = info.get("name", "")
        except (JiraError, Exception) as exc:
            logger.debug("projects.add_project: Jira lookup for %s failed (%s); proceeding without name", key, exc)

    project, already_existed = _store().add_project(key, name=project_name)
    return AddProjectResponse(project=project, already_existed=already_existed)


@router.delete("/{key}", response_model=RemoveProjectResponse)
def remove_project(key: str):
    try:
        validate_project_key(key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _store().remove_project(key)
    return RemoveProjectResponse(
        jira_project_key=key,
        message=f"Project {key} removed from registry. Checklist files retained.",
    )


# ---------------------------------------------------------------------------
# Project checklist routes (registered after /default/* to avoid shadowing)
# ---------------------------------------------------------------------------


@router.get("/{key}/checklist", response_model=ChecklistContentResponse)
def get_project_checklist(key: str):
    try:
        validate_project_key(key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _store().get_checklist_content_response(key=key)


@router.put("/{key}/checklist", response_model=ChecklistSaveResponse)
def save_project_checklist(key: str, body: ChecklistSaveRequest):
    try:
        validate_project_key(key)
        return _store().save_checklist(key=key, content=body.content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{key}/checklist/history", response_model=ChecklistHistoryResponse)
def get_project_checklist_history(key: str):
    try:
        validate_project_key(key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _store().list_checklist_versions(key=key)


@router.get("/{key}/checklist/history/{version}", response_model=ChecklistVersionContentResponse)
def get_project_checklist_version(key: str, version: int):
    try:
        validate_project_key(key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        return _store().load_checklist_version(key=key, version=version)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{key}/checklist/restore/{version}", response_model=ChecklistSaveResponse)
def restore_project_checklist_version(key: str, version: int):
    try:
        validate_project_key(key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        return _store().restore_checklist_version(key=key, version=version)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{key}/checklist", response_model=DeleteChecklistResponse)
def delete_project_checklist(key: str):
    """Permanently delete a project's checklist files. Never allowed for 'default'."""
    if key == "default":
        raise HTTPException(status_code=400, detail="The default checklist files cannot be deleted.")
    try:
        validate_project_key(key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        return _store().delete_checklist_files(key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
