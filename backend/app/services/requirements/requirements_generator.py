"""Bounded requirements-draft generation for Requirements Studio."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from backend.app.config import get_settings
from backend.app.services.llm_client import LLMClient, LLMError
from backend.app.services.prompt_loader import PromptLoader
from backend.app.services.requirements.models import ContextPack, FeatureWorkspace, RequirementsDraft

logger = logging.getLogger(__name__)


class RequirementsGenerator:
    """Generate a structured RequirementsDraft from a workspace and context pack."""

    def __init__(self, llm: LLMClient, prompt_loader: PromptLoader) -> None:
        self._llm = llm
        self._prompt_loader = prompt_loader
        self._settings = get_settings()

    def generate(self, workspace: FeatureWorkspace, context_pack: ContextPack) -> RequirementsDraft:
        self._ensure_generation_enabled()
        schema_hint = json.dumps(RequirementsDraft.model_json_schema(), indent=2)
        system = self._prompt_loader.load_prompt("requirements_generation.md")
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": self._build_prompt(workspace, context_pack, schema_hint),
            },
        ]
        data = self._llm.chat_json(
            messages,
            model_name=self._settings.requirements_generation_model_name,
            temperature=0.1,
        )
        data["draft_id"] = f"reqdraft-{workspace.workspace_id}"
        data["workspace_id"] = workspace.workspace_id
        data["generated_at"] = datetime.now(timezone.utc).isoformat()
        draft = RequirementsDraft(**data)
        for index, item in enumerate(draft.requirements, start=1):
            item.requirement_id = f"REQ-{index:03d}"
        return draft

    def _build_prompt(self, workspace: FeatureWorkspace, context_pack: ContextPack, schema_hint: str) -> str:
        context_blocks: list[str] = []
        for hit in context_pack.search_hits[:8]:
            context_blocks.append(
                f"[{hit.ref_id}] {hit.title}\nSnippet: {hit.snippet}\nMatched terms: "
                f"{', '.join(str(term) for term in hit.metadata.get('matched_terms', []))}"
            )
        for item in context_pack.pinned_items[:8]:
            context_blocks.append(
                f"[{item.ref_id}] {item.title}\nPinned rationale: {item.rationale or 'n/a'}"
            )

        return (
            "Generate a structured requirements draft using only the provided workspace request and context.\n"
            "Return ONLY valid JSON matching the schema below.\n\n"
            f"Schema:\n{schema_hint}\n\n"
            f"Workspace Title: {workspace.title}\n"
            f"Project Key: {workspace.project_key or 'n/a'}\n"
            f"Request Summary: {workspace.request_summary or 'n/a'}\n"
            f"Request Text:\n{workspace.request_text}\n\n"
            f"Context Pack Summary:\n{context_pack.summary_text}\n\n"
            "Selected Source Refs:\n"
            f"{json.dumps(context_pack.selected_source_refs, indent=2)}\n\n"
            "Context Excerpts:\n"
            f"{'\n\n'.join(context_blocks) if context_blocks else 'No context excerpts were available.'}\n\n"
            "Requirements should be practical, bounded, and traceable to source_refs."
        )

    def _ensure_generation_enabled(self) -> None:
        if not self._settings.requirements_generation_enabled:
            raise ValueError("Requirements generation is disabled by configuration.")
        if not (
            self._settings.llm_api_key
            or (self._settings.llm_access_key and self._settings.llm_secret_key)
        ):
            raise LLMError("LLM configuration is missing. Set LLM_API_KEY or access/secret credentials.")
