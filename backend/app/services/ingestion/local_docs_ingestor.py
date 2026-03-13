"""
Local filesystem document ingestion source.

Scans a directory for .txt, .md, .docx, and .xlsx files and normalises each
into an ArtifactRecord. Parser imports are deferred to keep startup fast and
avoid loading python-docx / openpyxl unless actually needed.

Deterministic artifact IDs (local-{sanitized_relative_path}) ensure idempotent
re-runs — re-ingesting the same file updates the existing record rather than
creating a duplicate.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.app.config import get_settings
from backend.app.services.graph.models import (
    ArtifactKind,
    ArtifactMetadata,
    ArtifactRecord,
    SourceSystem,
    SourceType,
)
from backend.app.services.ingestion.base import BaseIngestionSource
from backend.app.services.ingestion.raw_store import get_raw_store

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({".txt", ".md", ".docx", ".xlsx"})


def _make_artifact_id(root: Path, file_path: Path) -> str:
    """Return a stable, URL-safe artifact ID derived from the file's path relative to root."""
    try:
        rel = file_path.relative_to(root)
    except ValueError:
        rel = file_path
    sanitized = re.sub(r"[^\w-]", "-", rel.with_suffix("").as_posix()).lower()
    sanitized = re.sub(r"-{2,}", "-", sanitized).strip("-")
    return f"local-{sanitized}"


def _dispatch_parser(path: Path) -> tuple[str, dict[str, Any]]:
    """Dispatch to the appropriate parser based on file extension.

    Imports are deferred to avoid loading docx/xlsx at startup.
    """
    ext = path.suffix.lower()
    if ext in {".txt", ".md"}:
        from backend.app.services.parsers import text_parser
        return text_parser.extract_text(path)
    if ext == ".docx":
        from backend.app.services.parsers import docx_parser
        return docx_parser.extract_text(path)
    if ext == ".xlsx":
        from backend.app.services.parsers import xlsx_parser
        return xlsx_parser.extract_text(path)
    raise ValueError(f"Unsupported extension: {ext}")


class LocalDocsIngestionSource(BaseIngestionSource):
    """Ingests local filesystem documents into ArtifactRecord objects."""

    @property
    def source_name(self) -> str:
        return "local-docs"

    @property
    def source_type(self) -> SourceType:
        return SourceType.DOCUMENT

    def health_check(self) -> bool:
        return get_settings().knowledge_local_docs_dir.exists()

    def fetch_artifacts(self, run_id: str, **kwargs: Any) -> list[ArtifactRecord]:
        """Scan a directory and return ArtifactRecords for each supported file.

        kwargs:
          root_dir (str): Override the default scan directory.
          project_key (str): Project key to attach to each artifact's metadata.
          recursive (bool): Scan subdirectories (default True).
        """
        settings = get_settings()
        project_key: str | None = kwargs.get("project_key")
        raw_store = get_raw_store() if settings.knowledge_raw_capture_enabled else None

        root = (
            Path(kwargs["root_dir"]) if kwargs.get("root_dir")
            else settings.knowledge_local_docs_dir
        )
        if not root.exists():
            logger.warning("LocalDocsIngestionSource: root dir does not exist: %s", root)
            return []

        pattern = "**/*" if kwargs.get("recursive", True) else "*"
        candidates = [
            p for p in root.glob(pattern)
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
        ]

        artifacts: list[ArtifactRecord] = []
        for file_path in candidates:
            try:
                stat = file_path.stat()
                if stat.st_size > settings.knowledge_max_file_bytes:
                    logger.warning(
                        "LocalDocsIngestionSource: skipping %s — size %d exceeds limit of %d bytes",
                        file_path,
                        stat.st_size,
                        settings.knowledge_max_file_bytes,
                    )
                    continue

                text, parser_meta = _dispatch_parser(file_path)
                artifact_id = _make_artifact_id(root, file_path)
                raw_ref = (
                    raw_store.save_local_raw(
                        run_id, file_path, artifact_id + file_path.suffix.lower()
                    )
                    if raw_store
                    else None
                )
                created_at = datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc)
                updated_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)

                extra: dict[str, Any] = {
                    "original_path": str(file_path),
                    "file_name": file_path.name,
                    "extension": file_path.suffix.lower(),
                    "size_bytes": stat.st_size,
                    "parser_used": parser_meta.get("parser", "unknown"),
                }
                if "sheet_names" in parser_meta:
                    extra["worksheet_names"] = parser_meta["sheet_names"]

                meta = ArtifactMetadata(
                    artifact_id=artifact_id,
                    source_type=SourceType.DOCUMENT,
                    source_system=SourceSystem.LOCAL,
                    external_id=str(file_path),
                    project_key=project_key,
                    title=file_path.stem,
                    artifact_kind=ArtifactKind.SPECIFICATION,  # sensible default for local docs
                    created_at=created_at,
                    updated_at=updated_at,
                    ingestion_run_id=run_id,
                )
                artifacts.append(
                    ArtifactRecord(metadata=meta, text_content=text, raw_ref=raw_ref, extra=extra)
                )
            except Exception as exc:
                logger.warning("LocalDocsIngestionSource: skipping %s (%s)", file_path, exc)

        return artifacts


def get_local_docs_ingestion_source() -> LocalDocsIngestionSource:
    return LocalDocsIngestionSource()
