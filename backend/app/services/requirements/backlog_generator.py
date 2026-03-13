"""Bounded backlog decomposition generation for Requirements Studio."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from backend.app.config import get_settings
from backend.app.services.llm_client import LLMClient, LLMError
from backend.app.services.prompt_loader import PromptLoader
from backend.app.services.requirements.models import (
    BacklogDraft,
    BacklogItemType,
    ContextPack,
    FeatureWorkspace,
    RequirementsDraft,
)

logger = logging.getLogger(__name__)

_VALID_SPLIT_MODES = {"balanced", "coarse", "detailed"}


class BacklogGenerator:
    """Generate a structured backlog draft from a requirements draft."""

    def __init__(self, llm: LLMClient, prompt_loader: PromptLoader) -> None:
        self._llm = llm
        self._prompt_loader = prompt_loader
        self._settings = get_settings()

    def generate(
        self,
        workspace: FeatureWorkspace,
        requirements_draft: RequirementsDraft,
        context_pack: ContextPack | None = None,
        split_mode: str | None = None,
    ) -> BacklogDraft:
        self._ensure_generation_enabled()
        default_split_mode = (
            self._settings.requirements_default_story_split_mode
            if self._settings.requirements_default_story_split_mode in _VALID_SPLIT_MODES
            else "balanced"
        )
        resolved_split_mode = split_mode if split_mode in _VALID_SPLIT_MODES else default_split_mode
        schema_hint = json.dumps(BacklogDraft.model_json_schema(), indent=2)
        system = self._prompt_loader.load_prompt("backlog_generation.md")
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": self._build_prompt(workspace, requirements_draft, context_pack, resolved_split_mode, schema_hint),
            },
        ]
        data = self._llm.chat_json(
            messages,
            model_name=self._settings.requirements_generation_model_name,
            temperature=0.1,
        )
        data["backlog_id"] = f"backlog-{workspace.workspace_id}"
        data["workspace_id"] = workspace.workspace_id
        data["split_mode"] = resolved_split_mode
        data["generated_at"] = datetime.now(timezone.utc).isoformat()
        draft = BacklogDraft(**data)
        story_like = 0
        for index, item in enumerate(draft.items, start=1):
            item.item_id = f"{item.item_type.value.upper()}-{index:03d}"
            if item.item_type in {BacklogItemType.STORY, BacklogItemType.TASK}:
                story_like += 1
        if story_like > self._settings.requirements_story_max_count:
            raise ValueError(
                f"Generated backlog exceeded configured story/task cap of "
                f"{self._settings.requirements_story_max_count}."
            )
        return draft

    def _build_prompt(
        self,
        workspace: FeatureWorkspace,
        requirements_draft: RequirementsDraft,
        context_pack: ContextPack | None,
        split_mode: str,
        schema_hint: str,
    ) -> str:
        context_summary = context_pack.summary_text if context_pack is not None else "No context pack available."
        return (
            "Generate a practical backlog decomposition from the structured requirements below.\n"
            "Return ONLY valid JSON matching the schema below.\n\n"
            f"Schema:\n{schema_hint}\n\n"
            f"Workspace Title: {workspace.title}\n"
            f"Project Key: {workspace.project_key or 'n/a'}\n"
            f"Split Mode: {split_mode}\n\n"
            f"Context Summary:\n{context_summary}\n\n"
            "Requirements Draft:\n"
            f"{requirements_draft.model_dump_json(indent=2)}\n\n"
            "Keep the backlog bounded, traceable through source_refs, and suitable for later Jira publishing."
        )

    def _ensure_generation_enabled(self) -> None:
        if not self._settings.requirements_generation_enabled:
            raise ValueError("Requirements generation is disabled by configuration.")
        if not (
            self._settings.llm_api_key
            or (self._settings.llm_access_key and self._settings.llm_secret_key)
        ):
            raise LLMError("LLM configuration is missing. Set LLM_API_KEY or access/secret credentials.")
