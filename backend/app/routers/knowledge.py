"""
Knowledge layer router — debug/inspection and ingestion trigger endpoints for Phase 0–1.

All endpoints are under /api/knowledge and return JSON.
No auth is applied (consistent with other API routers in this project).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from backend.app.schemas_knowledge import JiraIngestRequest, LocalDocsIngestRequest
from backend.app.services.graph.models import ArtifactKind, SourceSystem
from backend.app.services.knowledge_service import get_knowledge_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


def _svc():
    return get_knowledge_service()


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@router.get("/health")
def knowledge_health():
    """Return a basic health response confirming the knowledge layer is reachable."""
    return {"status": "ok", "storage": "file-based", "layer": "knowledge", "phase": 0}


# ---------------------------------------------------------------------------
# Ingestion runs
# ---------------------------------------------------------------------------


@router.get("/runs")
def list_runs():
    """List all ingestion runs."""
    return [r.model_dump(mode="json") for r in _svc().list_runs()]


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


@router.get("/artifacts")
def list_artifacts():
    """List all ingested artifacts."""
    return [r.model_dump(mode="json") for r in _svc().list_artifacts()]


@router.get("/artifacts/search")
def search_artifacts(
    project_key: str | None = Query(default=None),
    source_system: SourceSystem | None = Query(default=None),
    artifact_kind: ArtifactKind | None = Query(default=None),
    title_contains: str | None = Query(default=None),
):
    """Search artifacts by project_key, source_system, artifact_kind, and/or title substring."""
    results = _svc().search_artifacts(
        project_key=project_key,
        source_system=source_system,
        artifact_kind=artifact_kind,
        title_contains=title_contains,
    )
    return [r.model_dump(mode="json") for r in results]


@router.get("/artifacts/{artifact_id}")
def get_artifact(artifact_id: str):
    """Return a single artifact by ID, or 404 if not found."""
    record = _svc().get_artifact(artifact_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Artifact '{artifact_id}' not found.")
    return record.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Chunks
# ---------------------------------------------------------------------------


@router.get("/chunks")
def list_chunks(artifact_id: str | None = None):
    """List chunks, optionally filtered to a single artifact_id query param."""
    return [r.model_dump(mode="json") for r in _svc().list_chunks(artifact_id=artifact_id)]


# ---------------------------------------------------------------------------
# Edges
# ---------------------------------------------------------------------------


@router.get("/edges")
def list_edges():
    """List all graph edges."""
    return [e.model_dump(mode="json") for e in _svc().list_edges()]


# ---------------------------------------------------------------------------
# Bootstrap (dev only)
# ---------------------------------------------------------------------------


@router.get("/bootstrap")
def bootstrap():
    """Create deterministic sample records for Phase 0 local validation. Idempotent."""
    ids = _svc().bootstrap_sample_data()
    logger.info("knowledge bootstrap: created sample records %s", ids)
    return ids


# ---------------------------------------------------------------------------
# Ingestion triggers (Phase 1 — local dev only)
# ---------------------------------------------------------------------------


@router.post("/ingest/jira")
def ingest_jira(body: JiraIngestRequest):
    """Trigger a Jira ingestion run. Returns the completed IngestionRun as JSON."""
    try:
        run = _svc().run_jira_ingestion(
            project_key=body.project_key,
            jql=body.jql,
            max_results=body.max_results,
        )
        return run.model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("ingest_jira: %s", exc)
        raise HTTPException(status_code=500, detail=f"Ingestion error: {exc}") from exc


@router.post("/ingest/local-docs")
def ingest_local_docs(body: LocalDocsIngestRequest):
    """Trigger a local document ingestion run. Returns the completed IngestionRun as JSON."""
    try:
        run = _svc().run_local_docs_ingestion(
            root_dir=body.root_dir,
            project_key=body.project_key,
            recursive=body.recursive,
        )
        return run.model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("ingest_local_docs: %s", exc)
        raise HTTPException(status_code=500, detail=f"Ingestion error: {exc}") from exc
