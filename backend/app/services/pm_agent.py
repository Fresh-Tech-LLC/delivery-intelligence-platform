"""
PM Agent - translates natural language to JQL and executes the query.
"""
from __future__ import annotations

import json
import logging
import re

from backend.app.schemas import JiraIssueResult, PMQueryResponse
from backend.app.services.jira_client import JiraClient, JiraError
from backend.app.services.llm_client import LLMClient, LLMError
from backend.app.services.prompt_loader import PromptLoader
from backend.app.utils import extract_json

logger = logging.getLogger(__name__)


class PMAgent:
    def __init__(
        self,
        llm: LLMClient,
        prompt_loader: PromptLoader,
        jira: JiraClient,
    ) -> None:
        self._llm = llm
        self._pl = prompt_loader
        self._jira = jira

    def query(self, session_id: str, natural_language_query: str) -> PMQueryResponse:
        system = self._pl.load_prompt("pm_jira_query.md")
        user_override = self._pl.load_pm_user_override().strip()
        if user_override:
            system += (
                "\n\n---\n## User Jira Context (Instance-Specific Override)\n"
                "Use the following details as authoritative for field names, team names, and conventions.\n\n"
                f"{user_override}"
            )
        project_key = self._jira._settings.jira_project_key or "not set"
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": (
                    f"Project key: {project_key}\n\n"
                    f"Query: {natural_language_query}\n\n"
                    "Return ONLY valid JSON object:\n"
                    '{"jql":"<single valid JQL string>"}\n'
                    "No markdown, no prose, no extra keys."
                ),
            },
        ]

        jql = self._generate_jql(messages, project_key)
        logger.info("PM Agent generated JQL: %s", jql)

        results: list[JiraIssueResult] = []
        total = 0

        if self._jira.is_configured():
            try:
                data = self._jira.search_issues(jql)
            except JiraError as exc:
                # Jira HTTP 400 usually means JQL syntax/field validation failure.
                if exc.status_code == 400:
                    repaired = self._repair_jql(
                        messages,
                        jql,
                        f"Jira validation error: {exc}",
                        project_key,
                    )
                    if repaired != jql:
                        jql = repaired
                        logger.info("PM Agent repaired JQL after Jira 400: %s", jql)
                    data = self._jira.search_issues(jql)
                else:
                    raise

            total = data.get("total", 0)
            for issue in data.get("issues", []):
                fields = issue.get("fields", {})
                assignee_field = fields.get("assignee")
                priority_field = fields.get("priority")
                results.append(
                    JiraIssueResult(
                        key=issue["key"],
                        summary=fields.get("summary", ""),
                        status=fields.get("status", {}).get("name", ""),
                        issue_type=fields.get("issuetype", {}).get("name", ""),
                        assignee=assignee_field.get("displayName") if assignee_field else None,
                        priority=priority_field.get("name") if priority_field else None,
                    )
                )
        else:
            logger.warning("Jira not configured - returning JQL only.")

        return PMQueryResponse(
            session_id=session_id,
            jql=jql,
            results=results,
            total=total,
        )

    def _generate_jql(self, base_messages: list[dict[str, str]], project_key: str) -> str:
        try:
            data = self._llm.chat_json(base_messages, temperature=0.0, max_tokens=256)
            jql = self._sanitize_jql(str(data.get("jql", "")))
        except LLMError as exc:
            logger.warning("PM Agent JSON-mode response failed; falling back to plain text JQL parse: %s", exc)
            raw = self._llm.chat(base_messages, temperature=0.0, max_tokens=256)
            jql = self._extract_jql_from_raw(raw)
        jql = self._fix_common_jql_typos(jql)
        errors = self._lexical_errors(jql)
        if not errors:
            return jql
        return self._repair_jql(base_messages, jql, "; ".join(errors), project_key)

    def _repair_jql(
        self,
        base_messages: list[dict[str, str]],
        current_jql: str,
        reason: str,
        project_key: str,
    ) -> str:
        repair_messages = base_messages + [
            {"role": "assistant", "content": json.dumps({"jql": current_jql})},
            {
                "role": "user",
                "content": (
                    "The previous JQL is invalid or risky.\n"
                    f"Reason: {reason}\n\n"
                    "Return ONLY valid JSON object:\n"
                    '{"jql":"<single valid JQL string>"}\n'
                    "No markdown, no prose, no extra keys."
                ),
            },
        ]
        try:
            repaired = self._llm.chat_json(repair_messages, temperature=0.0, max_tokens=256)
            jql = self._sanitize_jql(str(repaired.get("jql", "")))
        except LLMError as exc:
            logger.warning("PM Agent repair JSON-mode failed; falling back to plain text JQL parse: %s", exc)
            raw = self._llm.chat(repair_messages, temperature=0.0, max_tokens=256)
            jql = self._extract_jql_from_raw(raw)
        jql = self._fix_common_jql_typos(jql)
        if self._lexical_errors(jql):
            return self._fallback_jql(project_key)
        return jql

    def _sanitize_jql(self, value: str) -> str:
        return " ".join(value.strip().strip("`").strip('"').strip("'").split())

    def _fix_common_jql_typos(self, jql: str) -> str:
        replacements = {
            r"\bpririty\b": "priority",
            r"\bpriorit\b": "priority",
            r"\bissue_type\b": "issuetype",
            r"\bissue type\b": "issuetype",
        }
        out = jql
        for pattern, replacement in replacements.items():
            out = re.sub(pattern, replacement, out, flags=re.IGNORECASE)
        return out

    def _lexical_errors(self, jql: str) -> list[str]:
        if not jql:
            return ["empty JQL"]
        errors: list[str] = []
        if jql.count('"') % 2 != 0:
            errors.append("unmatched double quote")
        if jql.count("(") != jql.count(")"):
            errors.append("unbalanced parentheses")
        if re.search(r"(=|!=|>=|<=|>|<|~)\s*$", jql):
            errors.append("trailing operator")
        if re.search(r"\b(AND|OR|NOT|IN|IS|WAS|ORDER BY)\s*$", jql, flags=re.IGNORECASE):
            errors.append("trailing keyword")
        return errors

    def _fallback_jql(self, project_key: str) -> str:
        if project_key and project_key != "not set":
            return f'project = "{project_key}" ORDER BY updated DESC'
        return "ORDER BY updated DESC"

    def _extract_jql_from_raw(self, raw: str) -> str:
        text = raw.strip()
        # If provider returned JSON-like text without honoring JSON mode, extract it.
        try:
            parsed = extract_json(text)
            if isinstance(parsed, dict) and "jql" in parsed:
                return self._sanitize_jql(str(parsed["jql"]))
        except ValueError:
            pass

        # Common fallback: response includes a labeled line like "JQL: ...".
        labeled = re.search(r"^\s*JQL\s*:\s*(.+)$", text, flags=re.IGNORECASE | re.MULTILINE)
        if labeled:
            return self._sanitize_jql(labeled.group(1))
        return self._sanitize_jql(text)
