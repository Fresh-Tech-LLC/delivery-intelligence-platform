"""
BA Agent — orchestrates the full BA workflow:
  - Requirements generation & update
  - Story set generation & update (strict JSON)
  - Readiness checks
  - Pull existing Jira story + readiness check against it
  - Approve Jira story with ai-approved label + comment
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from backend.app.schemas import (
    PulledJiraStory,
    ReadinessFinding,
    ReadinessReport,
    RequirementsResponse,
    Severity,
    StoriesResponse,
    StorySet,
)
from backend.app.services.document_store import DocumentStore
from backend.app.services.jira_client import JiraClient, JiraError
from backend.app.services.llm_client import LLMClient
from backend.app.services.prompt_loader import PromptLoader

logger = logging.getLogger(__name__)


class BAAgent:
    def __init__(
        self,
        llm: LLMClient,
        prompt_loader: PromptLoader,
        store: DocumentStore,
        jira: Optional[JiraClient] = None,
    ) -> None:
        self._llm = llm
        self._pl = prompt_loader
        self._store = store
        self._jira = jira

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _hidden_checklist(self) -> str:
        try:
            return self._pl.load_config("ba_hidden_checklist.md")
        except FileNotFoundError:
            logger.warning("Hidden BA checklist not found; proceeding without it.")
            return ""

    def _system_requirements(self) -> str:
        checklist = self._hidden_checklist()
        base = self._pl.load_prompt("ba_requirements.md")
        if checklist:
            base += f"\n\n---\n## Internal BA Checklist (not shown to user)\n{checklist}"
        return base

    def _system_stories(self) -> str:
        checklist = self._hidden_checklist()
        base = self._pl.load_prompt("ba_story_breakdown.md")
        if checklist:
            base += f"\n\n---\n## Internal BA Checklist (not shown to user)\n{checklist}"
        return base

    def _system_readiness(self, session_id: str, jira_project_key: str = "") -> str:
        checklist = self._hidden_checklist()
        base = self._store.resolve_checklist(jira_project_key)
        uploaded = self._store.load_all_docs_text(session_id)
        if checklist:
            base += f"\n\n---\n## Internal BA Checklist\n{checklist}"
        if uploaded:
            base += f"\n\n---\n## Uploaded Project Documentation\n{uploaded}"
        return base

    # ------------------------------------------------------------------
    # Requirements
    # ------------------------------------------------------------------

    def generate_requirements(self, session_id: str, raw_notes: str) -> RequirementsResponse:
        ws = self._store.load_workspace(session_id)
        ws.raw_notes = raw_notes
        self._store.save_workspace(ws)  # persist before LLM call so notes survive errors

        system = self._system_requirements()
        user_content = (
            "Please process the following raw notes into structured requirements.\n\n"
            f"## Raw Notes\n{raw_notes}"
        )
        if ws.context_docs:
            doc_block = "\n\n".join(
                f"--- Uploaded Document: {name} ---\n{text}"
                for name, text in ws.context_docs.items()
                if text.strip()
            )
            if doc_block:
                user_content += f"\n\n## Uploaded Supporting Documents\n{doc_block}"
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]
        raw = self._llm.chat(messages, temperature=0.3)
        result = self._parse_requirements_output(raw)

        ws.requirements_draft = result.requirements
        self._store.save_workspace(ws)
        return RequirementsResponse(session_id=session_id, **result.model_dump(exclude={"session_id"}))

    def update_requirements(self, session_id: str, edit_instruction: str) -> RequirementsResponse:
        ws = self._store.load_workspace(session_id)
        if not ws.requirements_draft:
            raise ValueError("No requirements draft exists for this session. Generate requirements first.")

        system = self._system_requirements()
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": (
                    "Here is the current requirements document:\n\n"
                    f"{ws.requirements_draft}\n\n"
                    f"Please apply the following edit instruction:\n{edit_instruction}"
                ),
            },
        ]
        raw = self._llm.chat(messages, temperature=0.3)
        result = self._parse_requirements_output(raw)

        ws.requirements_draft = result.requirements
        self._store.save_workspace(ws)
        return RequirementsResponse(session_id=session_id, **result.model_dump(exclude={"session_id"}))

    def _parse_requirements_output(self, raw: str) -> RequirementsResponse:
        """
        Parse the LLM output for requirements. Expects structured text with
        sections for Requirements, Clarifying Questions, and Assumptions.
        Falls back gracefully.
        """
        import re

        requirements = raw
        questions: list[str] = []
        assumptions: list[str] = []

        # Try to extract sections by common heading patterns
        cq_match = re.search(
            r"#+\s*clarifying questions?\s*\n(.*?)(?=\n#+|\Z)", raw, re.IGNORECASE | re.DOTALL
        )
        if cq_match:
            block = cq_match.group(1)
            questions = [
                line.lstrip("-*•123456789. ").strip()
                for line in block.strip().splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]

        ass_match = re.search(
            r"#+\s*assumptions?\s*\n(.*?)(?=\n#+|\Z)", raw, re.IGNORECASE | re.DOTALL
        )
        if ass_match:
            block = ass_match.group(1)
            assumptions = [
                line.lstrip("-*•123456789. ").strip()
                for line in block.strip().splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]

        return RequirementsResponse(
            session_id="",
            requirements=requirements,
            clarifying_questions=questions,
            assumptions=assumptions,
        )

    # ------------------------------------------------------------------
    # Stories
    # ------------------------------------------------------------------

    def generate_stories(self, session_id: str) -> StoriesResponse:
        ws = self._store.load_workspace(session_id)
        if not ws.requirements_draft:
            raise ValueError("No requirements draft found. Generate requirements first.")

        system = self._system_stories()
        schema_hint = json.dumps(StorySet.model_json_schema(), indent=2)
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": (
                    "Generate a story set (1 Epic + Stories) from the requirements below.\n"
                    "Output ONLY valid JSON matching this schema (no markdown, no prose):\n"
                    f"{schema_hint}\n\n"
                    f"## Requirements\n{ws.requirements_draft}"
                ),
            },
        ]
        data = self._llm.chat_json(messages, temperature=0.1)
        story_set = StorySet(**data)

        ws.story_set = story_set.model_dump()
        self._store.save_workspace(ws)
        return StoriesResponse(session_id=session_id, story_set=story_set)

    def update_stories(self, session_id: str, edit_instruction: str) -> StoriesResponse:
        ws = self._store.load_workspace(session_id)
        if not ws.story_set:
            raise ValueError("No story set found. Generate stories first.")

        system = self._system_stories()
        schema_hint = json.dumps(StorySet.model_json_schema(), indent=2)
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": (
                    "Here is the current story set JSON:\n"
                    f"{json.dumps(ws.story_set, indent=2)}\n\n"
                    f"Apply this edit instruction: {edit_instruction}\n\n"
                    "Output ONLY valid JSON matching this schema:\n"
                    f"{schema_hint}"
                ),
            },
        ]
        data = self._llm.chat_json(messages, temperature=0.1)
        story_set = StorySet(**data)

        ws.story_set = story_set.model_dump()
        self._store.save_workspace(ws)
        return StoriesResponse(session_id=session_id, story_set=story_set)

    # ------------------------------------------------------------------
    # Readiness
    # ------------------------------------------------------------------

    def check_readiness(self, session_id: str) -> "ReadinessResponse":
        from backend.app.schemas import ReadinessResponse

        ws = self._store.load_workspace(session_id)
        if not ws.requirements_draft:
            raise ValueError("No requirements draft found.")

        system = self._system_readiness(session_id, ws.jira_project_key)
        story_context = (
            json.dumps(ws.story_set, indent=2) if ws.story_set else "Not yet generated."
        )
        schema_hint = json.dumps(ReadinessReport.model_json_schema(), indent=2)
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": (
                    "Run a readiness check on the following requirements and story set.\n"
                    "Output ONLY valid JSON matching this schema:\n"
                    f"{schema_hint}\n\n"
                    f"## Requirements\n{ws.requirements_draft}\n\n"
                    f"## Story Set\n{story_context}"
                ),
            },
        ]
        data = self._llm.chat_json(messages, temperature=0.1)
        report = ReadinessReport(**data)

        ws.readiness_report = report.model_dump()
        self._store.save_workspace(ws)
        return ReadinessResponse(session_id=session_id, report=report)

    # ------------------------------------------------------------------
    # Existing Jira story flow
    # ------------------------------------------------------------------

    def pull_jira_story(self, session_id: str, jira_story_key: str) -> PulledJiraStory:
        """Fetch a Jira issue by key and normalize it to PulledJiraStory."""
        if not self._jira:
            raise JiraError("Jira is not configured.")

        raw = self._jira.get_issue(jira_story_key, expand="names")
        fields = raw.get("fields", {})
        # names dict maps field ID → display name (only present when expand=names)
        field_id_by_name: dict[str, str] = {
            v.lower(): k for k, v in (raw.get("names") or {}).items()
        }

        def _flatten_adf(node: Any) -> str:
            """Recursively flatten Atlassian Document Format to plain text."""
            if isinstance(node, str):
                return node
            if isinstance(node, dict):
                t = node.get("type", "")
                children = node.get("content", [])
                text = node.get("text", "")
                parts = [_flatten_adf(c) for c in children]
                inner = "".join(parts) if parts else text
                if t in ("paragraph", "heading"):
                    return inner + "\n"
                if t in ("listItem",):
                    return "- " + inner
                if t in ("bulletList", "orderedList"):
                    return inner + "\n"
                return inner
            if isinstance(node, list):
                return "".join(_flatten_adf(c) for c in node)
            return ""

        def _to_text(value: Any) -> str:
            if not value:
                return ""
            if isinstance(value, str):
                return value
            if isinstance(value, dict):
                return _flatten_adf(value).strip()
            return str(value)

        def _extract_string_field(f: Any) -> str:
            if not f:
                return ""
            if isinstance(f, dict):
                return f.get("displayName") or f.get("name") or f.get("value") or ""
            return str(f)

        description = _to_text(fields.get("description", ""))

        # Acceptance criteria: try dedicated custom field first, then description section.
        # Strategy 1: use expand=names reverse-lookup to find the field ID by display name.
        # Strategy 2: fall back to matching field keys directly against known name patterns.
        ac_raw = ""
        acceptance_criteria: list[str] = []
        _ac_display_names = {"acceptance criteria", "acceptance_criteria", "acceptancecriteria"}
        ac_field_id: Optional[str] = None
        for display_name_lower, fid in field_id_by_name.items():
            if display_name_lower.replace("_", "").replace(" ", "").replace("-", "") in {
                n.replace("_", "").replace(" ", "").replace("-", "") for n in _ac_display_names
            }:
                ac_field_id = fid
                break
        if ac_field_id and ac_field_id in fields:
            ac_raw = _to_text(fields[ac_field_id])
        else:
            # Fallback: match field key directly (works if key itself contains "acceptancecriteria")
            _ac_key_patterns = {"acceptancecriteria", "acceptancecriteria"}
            for fname, fval in fields.items():
                if fname.lower().replace("_", "").replace("-", "") in _ac_key_patterns:
                    ac_raw = _to_text(fval)
                    break

        if ac_raw:
            acceptance_criteria = [
                line.lstrip("-*•123456789. ").strip()
                for line in ac_raw.splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
        else:
            # Fallback: look for "Acceptance Criteria" / "AC:" section in description
            import re
            ac_match = re.search(
                r"(?:#+\s*)?(?:acceptance criteria|AC\s*:)\s*\n(.*?)(?=\n#+|\Z)",
                description,
                re.IGNORECASE | re.DOTALL,
            )
            if ac_match:
                block = ac_match.group(1)
                acceptance_criteria = [
                    line.lstrip("-*•123456789. ").strip()
                    for line in block.strip().splitlines()
                    if line.strip()
                ]
                ac_raw = block.strip()

        labels = fields.get("labels") or []
        components = [_extract_string_field(c) for c in (fields.get("components") or [])]

        story = PulledJiraStory(
            key=raw.get("key", jira_story_key),
            summary=fields.get("summary", ""),
            description=description,
            acceptance_criteria=acceptance_criteria,
            ac_raw=ac_raw,
            status=_extract_string_field(fields.get("status")),
            priority=_extract_string_field(fields.get("priority")),
            assignee=_extract_string_field(fields.get("assignee")),
            labels=labels,
            components=components,
            issue_type=_extract_string_field(fields.get("issuetype")),
            last_pulled_at=datetime.now(timezone.utc).isoformat(),
        )

        ws = self._store.load_workspace(session_id)
        ws.jira_story_key = story.key
        ws.pulled_jira_story = story.model_dump()
        self._store.save_workspace(ws)
        return story

    def check_readiness_from_jira_story(self, session_id: str) -> "ReadinessResponse":
        """Run a readiness check against the pulled Jira story stored in the workspace."""
        from backend.app.schemas import ReadinessResponse

        ws = self._store.load_workspace(session_id)
        if not ws.pulled_jira_story:
            raise ValueError("No pulled Jira story in workspace. Pull a story first.")

        story = PulledJiraStory(**ws.pulled_jira_story)
        system = self._system_readiness(session_id, ws.jira_project_key)
        schema_hint = json.dumps(ReadinessReport.model_json_schema(), indent=2)

        story_text = (
            f"Key: {story.key}\n"
            f"Issue Type: {story.issue_type}\n"
            f"Status: {story.status}\n"
            f"Priority: {story.priority}\n"
            f"Assignee: {story.assignee}\n"
            f"Labels: {', '.join(story.labels) if story.labels else 'none'}\n"
            f"Components: {', '.join(story.components) if story.components else 'none'}\n\n"
            f"## Summary\n{story.summary}\n\n"
            f"## Description\n{story.description or '(none)'}\n\n"
            f"## Acceptance Criteria\n"
            + (
                "\n".join(f"- {ac}" for ac in story.acceptance_criteria)
                if story.acceptance_criteria
                else story.ac_raw or "(none)"
            )
        )

        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": (
                    "Run a readiness check on the following Jira story.\n"
                    "Output ONLY valid JSON matching this schema:\n"
                    f"{schema_hint}\n\n"
                    f"## Jira Story\n{story_text}"
                ),
            },
        ]
        data = self._llm.chat_json(messages, temperature=0.1)
        report = ReadinessReport(**data)

        ws.readiness_report = report.model_dump()
        self._store.save_workspace(ws)
        return ReadinessResponse(session_id=session_id, report=report)

    def approve_jira_story(self, session_id: str) -> dict[str, Any]:
        """Stamp an existing Jira story as ai-approved (label + comment).

        Guards: story key must be set, readiness report must exist with score >= 90.
        Label add is idempotent; comments are not deduplicated.
        Returns: {"issue_key", "label_added", "comment_added", "errors"}
        """
        if not self._jira:
            raise JiraError("Jira is not configured.")

        ws = self._store.load_workspace(session_id)
        if not ws.jira_story_key:
            raise ValueError("No Jira story key in workspace. Pull a story first.")
        if not ws.readiness_report:
            raise ValueError("No readiness report found. Run a readiness check first.")

        report = ReadinessReport(**ws.readiness_report)
        if report.score < 90:
            raise ValueError(
                f"Readiness score {report.score}/100 is below the required threshold of 90."
            )

        issue_key = ws.jira_story_key
        errors: list[str] = []
        label_added = False
        comment_added = False

        # Check if label already present
        try:
            current = self._jira.get_issue(issue_key)
            existing_labels = current.get("fields", {}).get("labels") or []
            if "ai-approved" not in existing_labels:
                self._jira.update_issue_labels(issue_key, ["ai-approved"])
                label_added = True
        except JiraError as exc:
            errors.append(f"Label update failed: {exc}")

        # Add comment (not deduplicated for POC)
        comment_body = (
            f"✅ Delivery Navigator readiness approved — Score: {report.score}/100"
        )
        try:
            self._jira.add_comment(issue_key, comment_body)
            comment_added = True
        except JiraError as exc:
            errors.append(f"Comment failed: {exc}")

        return {
            "issue_key": issue_key,
            "label_added": label_added,
            "comment_added": comment_added,
            "errors": errors,
        }
