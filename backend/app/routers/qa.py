from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from backend.app.schemas_qa import (
    CreateQaWorkspaceRequest,
    GeneratePlaywrightTestsRequest,
    GenerateScenariosRequest,
    PromoteRegressionCandidateRequest,
    RecordExecutionResultRequest,
    StartExplorationRequest,
)
from backend.app.services.llm_client import LLMError
from backend.app.services.qa.qa_service import QaService, get_qa_service
from backend.app.services.requirements.requirements_service import get_requirements_service

logger = logging.getLogger(__name__)

_BASE = Path(__file__).resolve().parents[3]
templates = Jinja2Templates(directory=str(_BASE / "frontend" / "templates"))

router = APIRouter(tags=["qa"])


def _svc() -> QaService:
    return get_qa_service()


def _handle_error(exc: Exception) -> HTTPException:
    if isinstance(exc, LLMError):
        return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    logger.exception("Unexpected QA router error")
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal error")


def _status_code_for_exc(exc: Exception) -> int:
    if isinstance(exc, LLMError):
        return status.HTTP_502_BAD_GATEWAY
    if isinstance(exc, ValueError):
        return status.HTTP_400_BAD_REQUEST
    return status.HTTP_500_INTERNAL_SERVER_ERROR


def _workspace_context(request: Request, qa_workspace_id: str, *, result: str | None = None, error: str | None = None) -> dict[str, object]:
    service = _svc()
    workspace = service.get_qa_workspace(qa_workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail=f"QA workspace '{qa_workspace_id}' not found.")
    source_workspace = get_requirements_service().get_workspace(workspace.source_workspace_id)
    scenarios = service.get_scenarios(qa_workspace_id)
    nl_scripts = service.get_nl_scripts(qa_workspace_id)
    execution_specs = service.get_execution_specs(qa_workspace_id)
    generated_tests = service.get_generated_tests(qa_workspace_id)
    exploration_runs = service.get_exploration_runs(qa_workspace_id)
    run_results = service.get_run_results(qa_workspace_id)
    regression_candidates = service.get_regression_candidates(qa_workspace_id)
    return {
        "request": request,
        "session_id": None,
        "workspace": workspace,
        "source_workspace": source_workspace,
        "scenarios": scenarios,
        "nl_scripts": nl_scripts,
        "execution_specs": execution_specs,
        "generated_tests": generated_tests,
        "exploration_runs": exploration_runs,
        "run_results": run_results,
        "regression_candidates": regression_candidates,
        "scenarios_json": scenarios.model_dump_json(indent=2) if scenarios else "",
        "nl_scripts_json": nl_scripts.model_dump_json(indent=2) if nl_scripts else "",
        "execution_specs_json": execution_specs.model_dump_json(indent=2) if execution_specs else "",
        "generated_tests_json": generated_tests.model_dump_json(indent=2) if generated_tests else "",
        "result": result,
        "error": error,
    }


@router.get("/qa", response_class=HTMLResponse)
async def qa_index(request: Request):
    return templates.TemplateResponse(
        "qa_index.html",
        {
            "request": request,
            "session_id": None,
            "qa_workspaces": _svc().list_qa_workspaces(),
            "requirements_workspaces": get_requirements_service().list_workspaces(),
            "error": None,
        },
    )


@router.post("/qa/workspaces")
async def create_qa_workspace_page(source_workspace_id: str = Form(...)):
    try:
        workspace = _svc().create_qa_workspace(source_workspace_id)
    except Exception as exc:
        raise _handle_error(exc) from exc
    return RedirectResponse(url=f"/qa/workspaces/{workspace.qa_workspace_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/qa/workspaces/{qa_workspace_id}", response_class=HTMLResponse)
async def qa_workspace_page(request: Request, qa_workspace_id: str):
    return templates.TemplateResponse("qa_workspace.html", _workspace_context(request, qa_workspace_id))


@router.post("/qa/workspaces/{qa_workspace_id}/traceability", response_class=HTMLResponse)
async def build_traceability_page(request: Request, qa_workspace_id: str):
    try:
        _svc().build_traceability(qa_workspace_id)
        return templates.TemplateResponse("qa_workspace.html", _workspace_context(request, qa_workspace_id, result="Traceability rebuilt."))
    except Exception as exc:
        return templates.TemplateResponse("qa_workspace.html", _workspace_context(request, qa_workspace_id, error=str(exc)), status_code=_status_code_for_exc(exc))


@router.post("/qa/workspaces/{qa_workspace_id}/generate-scenarios", response_class=HTMLResponse)
async def generate_scenarios_page(request: Request, qa_workspace_id: str, force_rebuild_traceability: str = Form("false")):
    try:
        _svc().generate_scenarios(qa_workspace_id, force_rebuild_traceability=force_rebuild_traceability.lower() == "true")
        return templates.TemplateResponse("qa_workspace.html", _workspace_context(request, qa_workspace_id, result="Scenarios generated."))
    except Exception as exc:
        return templates.TemplateResponse("qa_workspace.html", _workspace_context(request, qa_workspace_id, error=str(exc)), status_code=_status_code_for_exc(exc))


@router.post("/qa/workspaces/{qa_workspace_id}/generate-nl-scripts", response_class=HTMLResponse)
async def generate_nl_scripts_page(request: Request, qa_workspace_id: str):
    try:
        _svc().generate_nl_scripts(qa_workspace_id)
        return templates.TemplateResponse("qa_workspace.html", _workspace_context(request, qa_workspace_id, result="Natural-language scripts generated."))
    except Exception as exc:
        return templates.TemplateResponse("qa_workspace.html", _workspace_context(request, qa_workspace_id, error=str(exc)), status_code=_status_code_for_exc(exc))


@router.post("/qa/workspaces/{qa_workspace_id}/generate-execution-specs", response_class=HTMLResponse)
async def generate_execution_specs_page(request: Request, qa_workspace_id: str):
    try:
        _svc().generate_execution_specs(qa_workspace_id)
        return templates.TemplateResponse("qa_workspace.html", _workspace_context(request, qa_workspace_id, result="Execution specs generated."))
    except Exception as exc:
        return templates.TemplateResponse("qa_workspace.html", _workspace_context(request, qa_workspace_id, error=str(exc)), status_code=_status_code_for_exc(exc))


@router.post("/qa/workspaces/{qa_workspace_id}/generate-playwright", response_class=HTMLResponse)
async def generate_playwright_page(request: Request, qa_workspace_id: str, overwrite_existing: str = Form("true")):
    try:
        _svc().generate_playwright_tests(qa_workspace_id, overwrite_existing=overwrite_existing.lower() == "true")
        return templates.TemplateResponse("qa_workspace.html", _workspace_context(request, qa_workspace_id, result="Playwright tests generated."))
    except Exception as exc:
        return templates.TemplateResponse("qa_workspace.html", _workspace_context(request, qa_workspace_id, error=str(exc)), status_code=_status_code_for_exc(exc))


@router.post("/qa/workspaces/{qa_workspace_id}/explore", response_class=HTMLResponse)
async def explore_page(
    request: Request,
    qa_workspace_id: str,
    title: str = Form(...),
    target_url: str = Form(""),
    starting_context: str = Form(""),
    steps_requested: int | None = Form(default=None),
    browser_role: str = Form(""),
):
    try:
        _svc().start_guided_exploration(
            qa_workspace_id,
            title=title,
            target_url=target_url or None,
            starting_context=starting_context or None,
            steps_requested=steps_requested,
            browser_role=browser_role or None,
        )
        return templates.TemplateResponse("qa_workspace.html", _workspace_context(request, qa_workspace_id, result="Exploration run recorded."))
    except Exception as exc:
        return templates.TemplateResponse("qa_workspace.html", _workspace_context(request, qa_workspace_id, error=str(exc)), status_code=_status_code_for_exc(exc))


@router.post("/qa/workspaces/{qa_workspace_id}/run-results", response_class=HTMLResponse)
async def record_run_result_page(
    request: Request,
    qa_workspace_id: str,
    status_text: str = Form(...),
    passed: bool = Form(...),
    execution_spec_id: str = Form(""),
    generated_test_id: str = Form(""),
    failure_summary: str = Form(""),
    evidence_summary: str = Form(""),
):
    try:
        _svc().record_execution_result(
            qa_workspace_id,
            status=status_text,
            passed=passed,
            execution_spec_id=execution_spec_id or None,
            generated_test_id=generated_test_id or None,
            failure_summary=failure_summary or None,
            evidence_summary=evidence_summary or None,
        )
        return templates.TemplateResponse("qa_workspace.html", _workspace_context(request, qa_workspace_id, result="Execution result recorded."))
    except Exception as exc:
        return templates.TemplateResponse("qa_workspace.html", _workspace_context(request, qa_workspace_id, error=str(exc)), status_code=_status_code_for_exc(exc))


@router.post("/qa/workspaces/{qa_workspace_id}/regression/promote", response_class=HTMLResponse)
async def promote_regression_page(
    request: Request,
    qa_workspace_id: str,
    candidate_id: str = Form(...),
    target_state: str = Form(...),
    rationale: str = Form(""),
):
    try:
        _svc().promote_regression_candidate(qa_workspace_id, candidate_id, target_state, rationale or None)
        return templates.TemplateResponse("qa_workspace.html", _workspace_context(request, qa_workspace_id, result="Regression candidate promoted."))
    except Exception as exc:
        return templates.TemplateResponse("qa_workspace.html", _workspace_context(request, qa_workspace_id, error=str(exc)), status_code=_status_code_for_exc(exc))


@router.get("/api/qa/workspaces")
async def list_qa_workspaces_api():
    return [item.model_dump(mode="json") for item in _svc().list_qa_workspaces()]


@router.get("/api/qa/workspaces/{qa_workspace_id}")
async def get_qa_workspace_api(qa_workspace_id: str):
    service = _svc()
    workspace = service.get_qa_workspace(qa_workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail=f"QA workspace '{qa_workspace_id}' not found.")
    return {
        "workspace": workspace.model_dump(mode="json"),
        "scenarios": service.get_scenarios(qa_workspace_id).model_dump(mode="json") if service.get_scenarios(qa_workspace_id) else None,
        "nl_scripts": service.get_nl_scripts(qa_workspace_id).model_dump(mode="json") if service.get_nl_scripts(qa_workspace_id) else None,
        "execution_specs": service.get_execution_specs(qa_workspace_id).model_dump(mode="json") if service.get_execution_specs(qa_workspace_id) else None,
        "generated_tests": service.get_generated_tests(qa_workspace_id).model_dump(mode="json") if service.get_generated_tests(qa_workspace_id) else None,
        "exploration_runs": [item.model_dump(mode="json") for item in service.get_exploration_runs(qa_workspace_id)],
        "run_results": [item.model_dump(mode="json") for item in service.get_run_results(qa_workspace_id)],
        "regression_candidates": [item.model_dump(mode="json") for item in service.get_regression_candidates(qa_workspace_id)],
    }


@router.post("/api/qa/workspaces")
async def create_qa_workspace_api(body: CreateQaWorkspaceRequest):
    try:
        return _svc().create_qa_workspace(body.source_workspace_id).model_dump(mode="json")
    except Exception as exc:
        raise _handle_error(exc) from exc


@router.post("/api/qa/workspaces/{qa_workspace_id}/traceability")
async def build_traceability_api(qa_workspace_id: str):
    try:
        return [item.model_dump(mode="json") for item in _svc().build_traceability(qa_workspace_id)]
    except Exception as exc:
        raise _handle_error(exc) from exc


@router.post("/api/qa/workspaces/{qa_workspace_id}/generate-scenarios")
async def generate_scenarios_api(qa_workspace_id: str, body: GenerateScenariosRequest):
    try:
        return _svc().generate_scenarios(
            qa_workspace_id,
            force_rebuild_traceability=body.force_rebuild_traceability,
        ).model_dump(mode="json")
    except Exception as exc:
        raise _handle_error(exc) from exc


@router.post("/api/qa/workspaces/{qa_workspace_id}/generate-nl-scripts")
async def generate_nl_scripts_api(qa_workspace_id: str):
    try:
        return _svc().generate_nl_scripts(qa_workspace_id).model_dump(mode="json")
    except Exception as exc:
        raise _handle_error(exc) from exc


@router.post("/api/qa/workspaces/{qa_workspace_id}/generate-execution-specs")
async def generate_execution_specs_api(qa_workspace_id: str):
    try:
        return _svc().generate_execution_specs(qa_workspace_id).model_dump(mode="json")
    except Exception as exc:
        raise _handle_error(exc) from exc


@router.post("/api/qa/workspaces/{qa_workspace_id}/generate-playwright")
async def generate_playwright_api(qa_workspace_id: str, body: GeneratePlaywrightTestsRequest):
    try:
        return _svc().generate_playwright_tests(
            qa_workspace_id,
            overwrite_existing=body.overwrite_existing,
        ).model_dump(mode="json")
    except Exception as exc:
        raise _handle_error(exc) from exc


@router.post("/api/qa/workspaces/{qa_workspace_id}/explore")
async def start_exploration_api(qa_workspace_id: str, body: StartExplorationRequest):
    try:
        return _svc().start_guided_exploration(
            qa_workspace_id,
            title=body.title,
            target_url=body.target_url,
            starting_context=body.starting_context,
            steps_requested=body.steps_requested,
            browser_role=body.browser_role,
        ).model_dump(mode="json")
    except Exception as exc:
        raise _handle_error(exc) from exc


@router.post("/api/qa/workspaces/{qa_workspace_id}/run-results")
async def record_run_result_api(qa_workspace_id: str, body: RecordExecutionResultRequest):
    try:
        return _svc().record_execution_result(
            qa_workspace_id,
            status=body.status,
            passed=body.passed,
            execution_spec_id=body.execution_spec_id,
            generated_test_id=body.generated_test_id,
            failure_summary=body.failure_summary,
            evidence_summary=body.evidence_summary,
            evidence_type=body.evidence_type,
        ).model_dump(mode="json")
    except Exception as exc:
        raise _handle_error(exc) from exc


@router.post("/api/qa/workspaces/{qa_workspace_id}/regression/promote")
async def promote_regression_api(qa_workspace_id: str, body: PromoteRegressionCandidateRequest):
    try:
        return _svc().promote_regression_candidate(
            qa_workspace_id,
            body.candidate_id,
            body.target_state,
            body.rationale,
        ).model_dump(mode="json")
    except Exception as exc:
        raise _handle_error(exc) from exc
