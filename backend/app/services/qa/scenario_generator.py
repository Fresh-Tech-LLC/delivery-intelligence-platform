"""Bounded scenario generation for QA Studio."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from backend.app.config import get_settings
from backend.app.services.llm_client import LLMClient, LLMError
from backend.app.services.prompt_loader import PromptLoader
from backend.app.services.qa.models import ScenarioSet, ScenarioStatus
from backend.app.services.requirements.models import BacklogDraft, ContextPack, RequirementsDraft


class ScenarioGenerator:
    """Generate grounded QA scenarios from requirements, backlog, and traceability."""

    def __init__(self, llm: LLMClient, prompt_loader: PromptLoader) -> None:
        self._llm = llm
        self._prompt_loader = prompt_loader
        self._settings = get_settings()

    def generate(
        self,
        qa_workspace_id: str,
        title: str,
        requirements_draft: RequirementsDraft | None,
        backlog_draft: BacklogDraft | None,
        traceability_refs: list[str],
        context_pack: ContextPack | None = None,
    ) -> ScenarioSet:
        self._ensure_generation_enabled()
        if requirements_draft is None:
            raise ValueError("Requirements draft is required before generating scenarios.")
        system = self._prompt_loader.load_prompt("qa_scenario_generation.md")
        schema_hint = json.dumps(ScenarioSet.model_json_schema(), indent=2)
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": self._build_prompt(title, requirements_draft, backlog_draft, traceability_refs, context_pack, schema_hint),
            },
        ]
        data = self._llm.chat_json(
            messages,
            model_name=self._settings.qa_generation_model_name,
            temperature=0.1,
        )
        data["scenario_set_id"] = f"scenarios-{qa_workspace_id}"
        data["qa_workspace_id"] = qa_workspace_id
        data["generated_at"] = datetime.now(timezone.utc).isoformat()
        scenario_set = ScenarioSet(**data)
        if len(scenario_set.scenarios) > self._settings.qa_max_scenarios_per_workspace:
            scenario_set.scenarios = scenario_set.scenarios[: self._settings.qa_max_scenarios_per_workspace]
        for index, scenario in enumerate(scenario_set.scenarios, start=1):
            scenario.scenario_id = f"SCN-{index:03d}"
            scenario.qa_workspace_id = qa_workspace_id
            scenario.status = ScenarioStatus.DRAFT
        return scenario_set

    def _build_prompt(
        self,
        title: str,
        requirements_draft: RequirementsDraft,
        backlog_draft: BacklogDraft | None,
        traceability_refs: list[str],
        context_pack: ContextPack | None,
        schema_hint: str,
    ) -> str:
        return (
            "Generate grounded QA scenarios from the provided requirements and backlog.\n"
            "Return ONLY valid JSON matching the schema below.\n\n"
            f"Schema:\n{schema_hint}\n\n"
            f"Workspace Title: {title}\n"
            f"Requirements Draft:\n{requirements_draft.model_dump_json(indent=2)}\n\n"
            f"Backlog Draft:\n{backlog_draft.model_dump_json(indent=2) if backlog_draft else 'None'}\n\n"
            f"Traceability Refs:\n{json.dumps(traceability_refs, indent=2)}\n\n"
            f"Context Summary:\n{context_pack.summary_text if context_pack else 'None'}\n\n"
            "Keep scenarios specific, business-readable, and bounded."
        )

    def _ensure_generation_enabled(self) -> None:
        if not self._settings.qa_generation_enabled:
            raise ValueError("QA generation is disabled by configuration.")
        if not (
            self._settings.llm_api_key
            or (self._settings.llm_access_key and self._settings.llm_secret_key)
        ):
            raise LLMError("LLM configuration is missing. Set LLM_API_KEY or access/secret credentials.")
