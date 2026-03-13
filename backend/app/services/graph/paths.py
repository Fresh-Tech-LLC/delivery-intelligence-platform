"""
Centralised path helpers for the knowledge storage layer.

Storage layout under local_data/knowledge/:
  raw/                        — future: raw source dumps before normalisation
  normalized/artifacts/       — ArtifactRecord JSON files
  chunks/                     — ChunkRecord JSON files
  edges/                      — GraphEdge JSON files
  runs/                       — IngestionRun JSON files
"""
from __future__ import annotations

from pathlib import Path

from backend.app.config import get_settings


def knowledge_root() -> Path:
    """Root directory for all knowledge storage."""
    return get_settings().local_data_dir / "knowledge"


def raw_dir() -> Path:
    """Directory for raw source dumps (pre-normalisation)."""
    return knowledge_root() / "raw"


def artifacts_dir() -> Path:
    """Directory for normalised ArtifactRecord JSON files."""
    return knowledge_root() / "normalized" / "artifacts"


def chunks_dir() -> Path:
    """Directory for ChunkRecord JSON files."""
    return knowledge_root() / "chunks"


def edges_dir() -> Path:
    """Directory for GraphEdge JSON files."""
    return knowledge_root() / "edges"


def runs_dir() -> Path:
    """Directory for IngestionRun JSON files."""
    return knowledge_root() / "runs"


def ensure_knowledge_dirs() -> None:
    """Create all knowledge subdirectories if they do not already exist."""
    for d in (raw_dir(), artifacts_dir(), chunks_dir(), edges_dir(), runs_dir()):
        d.mkdir(parents=True, exist_ok=True)


def atomic_write(path: Path, content: str) -> None:
    """Write *content* to *path* via a temp file then replace (near-atomic on Windows).

    Normalises line endings to LF before writing so that content submitted
    from web forms (which may carry CRLF) does not accumulate extra blank lines.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content.replace("\r\n", "\n").replace("\r", "\n"), encoding="utf-8", newline="")
    tmp.replace(path)
