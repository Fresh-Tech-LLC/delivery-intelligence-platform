"""Natural-language QA script generation."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from backend.app.config import get_settings
from backend.app.services.llm_client import LLMClient, LLMError
from backend.app.services.prompt_loader import PromptLoader
from backend.app.services.qa.models import NaturalLanguageScriptSet


class NaturalLanguageScriptGenerator:
    """Generate QA-reviewable natural-language scripts from scenarios."""

    def __init__(self, llm: LLMClient, prompt_loader: PromptLoader) -> None:
        self._llm = llm
        self._prompt_loader = prompt_loader
        self._settings = get_settings()

    def generate(self, qa_workspace_id: str, scenarios_json: str) -> NaturalLanguageScriptSet:
        self._ensure_generation_enabled()
        system = self._prompt_loader.load_prompt("qa_nl_script_generation.md")
        schema_hint = json.dumps(NaturalLanguageScriptSet.model_json_schema(), indent=2)
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": (
                    "Convert the following QA scenarios into business-readable natural-language test scripts.\n"
                    "Return ONLY valid JSON matching the schema below.\n\n"
                    f"Schema:\n{schema_hint}\n\n"
                    f"Scenarios:\n{scenarios_json}\n"
                ),
            },
        ]
        data = self._llm.chat_json(
            messages,
            model_name=self._settings.qa_generation_model_name,
            temperature=0.1,
        )
        data["script_set_id"] = f"scripts-{qa_workspace_id}"
        data["qa_workspace_id"] = qa_workspace_id
        data["generated_at"] = datetime.now(timezone.utc).isoformat()
        script_set = NaturalLanguageScriptSet(**data)
        if len(script_set.scripts) > self._settings.qa_max_nl_scripts_per_run:
            script_set.scripts = script_set.scripts[: self._settings.qa_max_nl_scripts_per_run]
        for index, script in enumerate(script_set.scripts, start=1):
            script.script_id = f"NLS-{index:03d}"
            script.qa_workspace_id = qa_workspace_id
        return script_set

    def _ensure_generation_enabled(self) -> None:
        if not self._settings.qa_generation_enabled:
            raise ValueError("QA generation is disabled by configuration.")
        if not (
            self._settings.llm_api_key
            or (self._settings.llm_access_key and self._settings.llm_secret_key)
        ):
            raise LLMError("LLM configuration is missing. Set LLM_API_KEY or access/secret credentials.")
