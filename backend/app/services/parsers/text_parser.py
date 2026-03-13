"""Plain-text and Markdown parser."""
from __future__ import annotations

from pathlib import Path
from typing import Any


def extract_text(path: Path) -> tuple[str, dict[str, Any]]:
    """Return (extracted_text, metadata_dict) for .txt and .md files."""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text, {"line_count": text.count("\n") + 1, "parser": "text_parser"}
