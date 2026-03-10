"""
Batch Readiness Report router.

Runs the full LLM readiness check on a range of Jira issues (e.g. PROJ-100 → PROJ-200)
and produces a score-only table.  Only one job may run at a time (process-local singleton).
Cancellation is cooperative — a running LLM call will complete before the cancel takes effect.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.app.config import get_settings
from backend.app.schemas import (
    BatchJobStatus,
    BatchReadinessJob,
    BatchReadinessResult,
)
from backend.app.services.ba_agent import BAAgent
from backend.app.services.document_store import DocumentStore, _atomic_write
from backend.app.services.jira_client import JiraClient, JiraError
from backend.app.services.llm_client import get_llm_client
from backend.app.services.prompt_loader import get_prompt_loader

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/batch", tags=["Batch"])


# ---------------------------------------------------------------------------
# Process-local singleton (single-process deployment)
# ---------------------------------------------------------------------------


@dataclass
class _RunningJob:
    job: BatchReadinessJob
    cancel_flag: threading.Event


_active: _RunningJob | None = None
_active_lock = threading.Lock()


# ---------------------------------------------------------------------------
# File persistence
# ---------------------------------------------------------------------------


def _job_path() -> Path:
    return get_settings().data_dir / "batch_report" / "job.json"


def _save_job(job: BatchReadinessJob) -> None:
    path = _job_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, job.model_dump_json(indent=2))


def _load_job() -> BatchReadinessJob | None:
    path = _job_path()
    if not path.exists():
        return None
    try:
        job = BatchReadinessJob.model_validate_json(path.read_text(encoding="utf-8"))
        # Recover from server restart: file stuck in running state
        if job.status == BatchJobStatus.running:
            job.status = BatchJobStatus.cancelled
            job.error = "Interrupted by server restart"
            _save_job(job)
        return job
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Batch runner (sync, runs in thread pool via run_in_executor)
# ---------------------------------------------------------------------------


def _run_batch(job: BatchReadinessJob, cancel: threading.Event) -> None:
    global _active
    try:
        agent = BAAgent(
            llm=get_llm_client(),
            prompt_loader=get_prompt_loader(),
            store=DocumentStore(),
            jira=JiraClient(),
        )

        for i, num in enumerate(range(job.start_num, job.end_num + 1)):
            if cancel.is_set():
                job.status = BatchJobStatus.cancelled
                break

            key = f"{job.project_key}-{num}"
            job.current_key = key
            job.processed = i + 1
            _save_job(job)

            try:
                score, _ = agent.score_story(key, job.project_key)
                job.results.append(BatchReadinessResult(key=key, score=score))
                job.success_count += 1
            except JiraError as exc:
                err = "not found" if getattr(exc, "status_code", None) == 404 else str(exc)
                job.results.append(BatchReadinessResult(key=key, error=err))
                job.error_count += 1
            except Exception as exc:
                job.results.append(BatchReadinessResult(key=key, error=str(exc)))
                job.error_count += 1

            _save_job(job)
        else:
            # for/else: only when loop completes without a cancel break
            job.status = BatchJobStatus.done

    except Exception as exc:
        logger.exception("Batch runner unhandled error: %s", exc)
        job.status = BatchJobStatus.failed
        job.error = str(exc)
        # Partial results already in job.results — preserved in finally

    finally:
        job.finished_at = datetime.now(timezone.utc).isoformat()
        job.current_key = ""
        _save_job(job)
        with _active_lock:
            _active = None


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------


class BatchStartRequest(BaseModel):
    project_key: str
    start_num: int
    end_num: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/start")
async def batch_start(req: BatchStartRequest) -> BatchReadinessJob:
    global _active

    key = req.project_key.strip().upper()  # single canonical normalization point
    if not re.match(r"^[A-Z][A-Z0-9_\-]{0,49}$", key):
        raise HTTPException(422, "Invalid project key")
    if req.start_num < 1 or req.end_num < req.start_num:
        raise HTTPException(422, "end_num must be >= start_num >= 1")
    if req.end_num - req.start_num + 1 > 500:
        raise HTTPException(422, "Range cannot exceed 500 issues")

    with _active_lock:
        if _active is not None:
            raise HTTPException(409, "A batch job is already running")
        job = BatchReadinessJob(
            job_id=uuid.uuid4().hex,
            project_key=key,
            start_num=req.start_num,
            end_num=req.end_num,
            status=BatchJobStatus.running,
            total=req.end_num - req.start_num + 1,
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        cancel_flag = threading.Event()
        _active = _RunningJob(job=job, cancel_flag=cancel_flag)

    _save_job(job)
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, _run_batch, job, cancel_flag)
    return job


@router.post("/cancel")
async def batch_cancel() -> dict:
    with _active_lock:
        if _active is None:
            raise HTTPException(404, "No job is running")
        _active.cancel_flag.set()
    return {"message": "Cancel signal sent"}


@router.get("/status")
async def batch_status():
    with _active_lock:
        active = _active
    if active is not None:
        return active.job
    job = _load_job()
    if job is None:
        return {"status": "idle"}
    return job


# ---------------------------------------------------------------------------
# SSE stream
# ---------------------------------------------------------------------------


async def _batch_event_generator():
    while True:
        with _active_lock:
            active = _active

        if active is None:
            job = _load_job()
            if job:
                yield f"data: {json.dumps({'type': 'snapshot', 'job': job.model_dump()})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
            return

        job = active.job
        yield (
            f"data: {json.dumps({'type': 'progress', 'job_id': job.job_id, 'key': job.current_key, 'processed': job.processed, 'total': job.total, 'project_key': job.project_key, 'start_num': job.start_num, 'end_num': job.end_num})}\n\n"
        )

        if job.status in (BatchJobStatus.done, BatchJobStatus.cancelled, BatchJobStatus.failed):
            yield f"data: {json.dumps({'type': 'snapshot', 'job': job.model_dump()})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
            return

        await asyncio.sleep(0.5)


@router.get("/stream")
async def batch_stream() -> StreamingResponse:
    return StreamingResponse(
        _batch_event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
