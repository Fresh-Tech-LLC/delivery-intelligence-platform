"""
Admin routes for the Model Capability Probe feature.

Routes:
    GET  /admin/probe                       — index page (recent runs + start button)
    POST /admin/probe/runs                  — start a new probe run
    GET  /admin/probe/runs/{run_id}         — run detail page (HTML)
    GET  /admin/probe/runs/{run_id}/status  — JSON polling endpoint
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from backend.app.config import get_settings
from backend.app.services.capability_probe.probe_service import ProbeService, get_probe_service

logger = logging.getLogger(__name__)

_base = Path(__file__).resolve().parent.parent.parent.parent  # repo root
templates = Jinja2Templates(directory=str(_base / "frontend" / "templates"))

router = APIRouter(prefix="/admin/probe", tags=["Admin Probe"])


def _svc() -> ProbeService:
    return get_probe_service()


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------


@router.get("", response_class=HTMLResponse)
def probe_index(request: Request, error: str | None = None):
    svc = _svc()
    runs = svc.list_runs()
    active_run_id = svc.active_run_id()
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "admin/capability_probe_index.html",
        {
            "session_id": None,
            "runs": runs,
            "active_run_id": active_run_id,
            "error": error,
            "model_name": settings.llm_model_name,
            "llm_api_base": settings.llm_api_base,
        },
    )


# ---------------------------------------------------------------------------
# Start run
# ---------------------------------------------------------------------------


@router.post("/runs")
def start_probe_run(request: Request):
    svc = _svc()
    try:
        run = svc.create_run()
        svc.start_run(run.run_id)
        return RedirectResponse(
            url=f"/admin/probe/runs/{run.run_id}", status_code=303
        )
    except ValueError as exc:
        logger.warning("Cannot start probe run: %s", exc)
        return RedirectResponse(
            url="/admin/probe?error=already_running", status_code=303
        )
    except Exception as exc:
        logger.exception("Unexpected error starting probe run: %s", exc)
        return RedirectResponse(
            url="/admin/probe?error=start_failed", status_code=303
        )


# ---------------------------------------------------------------------------
# Run detail page
# ---------------------------------------------------------------------------


@router.get("/runs/{run_id}", response_class=HTMLResponse)
def probe_run_detail(request: Request, run_id: str):
    svc = _svc()
    run = svc.get_run(run_id)
    if run is None:
        return HTMLResponse(content="Run not found.", status_code=404)

    # Prefer steps stored on the run object; fall back to steps file.
    steps = run.steps if run.steps else svc._store.get_steps(run_id)
    report = svc.get_report(run_id)

    import json
    report_json = json.dumps(report.model_dump(mode="json"), indent=2) if report else None

    return templates.TemplateResponse(
        request,
        "admin/capability_probe_run.html",
        {
            "session_id": None,
            "run": run,
            "steps": steps,
            "report": report,
            "report_json": report_json,
        },
    )


# ---------------------------------------------------------------------------
# JSON polling endpoint
# ---------------------------------------------------------------------------


@router.get("/runs/{run_id}/status")
def probe_run_status(run_id: str):
    status = _svc().get_run_status(run_id)
    return JSONResponse(content=status)
