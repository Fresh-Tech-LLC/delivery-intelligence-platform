"""
Prompt loader — reads prompt files on every call (no caching) so that
editing a prompt file is reflected immediately without a server restart.
"""
from __future__ import annotations

import logging
from pathlib import Path
from string import Template

from backend.app.config import get_settings

logger = logging.getLogger(__name__)


class PromptLoader:
    def __init__(self) -> None:
        self._settings = get_settings()

    def _resolve(self, filename: str, directory: Path) -> Path:
        path = directory / filename
        if not path.exists():
            raise FileNotFoundError(f"Prompt file not found: {path}")
        return path

    def load(self, filename: str, *, directory: Path | None = None, **substitutions: str) -> str:
        """
        Load a prompt file and optionally perform $variable substitutions.

        Args:
            filename: file name relative to the prompts or config directory.
            directory: override directory (defaults to settings.prompts_dir).
            **substitutions: key=value pairs for $key substitution in the template.
        """
        base_dir = directory if directory is not None else self._settings.prompts_dir
        path = self._resolve(filename, base_dir)
        text = path.read_text(encoding="utf-8")
        if substitutions:
            try:
                text = Template(text).safe_substitute(**substitutions)
            except Exception as exc:
                logger.warning("Prompt substitution error in %s: %s", filename, exc)
        return text

    def load_prompt(self, filename: str, **substitutions: str) -> str:
        """Load from the prompts directory."""
        return self.load(filename, directory=self._settings.prompts_dir, **substitutions)

    def load_config(self, filename: str, **substitutions: str) -> str:
        """Load from the config directory (e.g. hidden checklist)."""
        return self.load(filename, directory=self._settings.config_dir, **substitutions)

    def load_pm_user_override(self) -> str:
        """
        Load optional user-maintained PM prompt override content.
        Returns empty string if file is missing/unreadable.
        """
        path = self._settings.pm_user_prompt_file
        if not path.exists():
            return ""
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            logger.warning("Failed to read PM user prompt override '%s': %s", path, exc)
            return ""
        max_chars = max(1, int(self._settings.pm_user_prompt_max_chars))
        if len(text) > max_chars:
            logger.warning(
                "PM user prompt override too large (%d chars). Truncating to %d.",
                len(text),
                max_chars,
            )
            text = text[:max_chars]
        return text


def get_prompt_loader() -> PromptLoader:
    return PromptLoader()
