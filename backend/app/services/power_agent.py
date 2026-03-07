"""
Power Jira Agent — two-stage discovery + agentic plan/action/observe loop.

Stage 1 (global bootstrap): lightweight, runs at start of each run() call, held in-memory.
Stage 2 (project discovery): runs on first Power Mode visit, stored in workspace.jira_project_ctx.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Generator
from datetime import datetime, timezone

from backend.app.schemas import (
    JiraFieldInfo,
    JiraProjectContext,
    PowerDiscoverResponse,
    PowerStepEvent,
)
from backend.app.services.document_store import DocumentStore
from backend.app.services.jira_client import JiraClient, JiraError
from backend.app.services.llm_client import LLMClient, LLMError
from backend.app.services.prompt_loader import PromptLoader

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 5
POWER_MAX_TOKENS = 4096
MAX_SUMMARY_CUSTOM_FIELDS = 30
MAX_SUMMARY_HINTS = 8

# Semantic alias detection — maps a stable label to candidate field name substrings (lowercase)
_SEMANTIC_FIELD_CANDIDATES: dict[str, list[str]] = {
    "developer":         ["developer", "dev", "developed by"],
    "tested_by":         ["tested by", "tester", "qa", "qa tester"],
    "team":              ["team", "squad", "tribe"],
    "sprint":            ["sprint", "sprint name"],
    "story_points":      ["story points", "story point", "points", "sp", "estimate", "story point estimate"],
    "epic_link":         ["epic link", "epic name", "epic"],
    "loe":               ["loe", "level of effort", "effort", "t-shirt size", "size"],
    "business_severity": ["business severity", "business priority", "biz severity"],
    "customer_impact":   ["customer impact", "customer priority"],
    "target_release":    ["target release", "target version", "planned release"],
    "risk":              ["risk", "risk level"],
    "category":          ["category", "issue category", "work category"],
    "environment":       ["environment", "affected environment"],
}

_TEAM_FIELD_NAMES = {"team", "squad", "tribe"}


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _perm_flag(perms_response: dict, key: str) -> bool:
    """Safely extract havePermission from a mypermissions response."""
    try:
        return bool(perms_response.get("permissions", {}).get(key, {}).get("havePermission", False))
    except Exception:
        return False


def _detect_semantic_aliases(all_fields: list[dict]) -> dict[str, str]:
    """Scan the global field list for semantically labelled custom fields."""
    name_to_id = {f.get("name", "").lower().strip(): f["id"] for f in all_fields}
    aliases: dict[str, str] = {}
    for alias, candidates in _SEMANTIC_FIELD_CANDIDATES.items():
        for candidate in candidates:
            if candidate in name_to_id:
                aliases[alias] = name_to_id[candidate]
                break
    return aliases


def _detect_team_field_id(all_fields: list[dict]) -> str | None:
    """Return the field ID for the Team/Squad/Tribe field, if one exists."""
    for f in all_fields:
        if f.get("name", "").lower().strip() in _TEAM_FIELD_NAMES:
            return f["id"]
    return None


def _discover_teams(jira: "JiraClient", pk: str, team_field_id: str) -> list[dict[str, str]]:
    """
    Adaptive time-windowed team discovery.

    Algorithm:
    1. Two setup queries to get total issue count and project date bounds.
    2. Divide the full date range into N evenly-spaced windows (N = 1–8, based on volume).
    3. Query each window for issues with the team field set, requesting only that field.
    4. Merge unique team name→ID pairs across all windows.

    Total API calls: 2 (setup) + N (windows) = 3–10 calls.
    Total issues sampled: N × results_per_window ≈ 100–800 with even temporal spread.
    """
    from datetime import datetime, timedelta

    base_jql = f'project = "{pk}" AND "{team_field_id}" is not EMPTY'

    # Setup pass 1: total count + oldest issue date
    try:
        oldest_data = jira.search_issues_post(
            f'{base_jql} ORDER BY created ASC', max_results=1,
            fields=[team_field_id, "created"],
        )
    except JiraError as exc:
        logger.warning("PowerAgent._discover_teams: setup query failed (%s)", exc)
        return []

    total_issues = oldest_data.get("total", len(oldest_data.get("issues", [])))
    if total_issues == 0:
        return []
    oldest_issues = oldest_data.get("issues", [])
    if not oldest_issues:
        return []
    oldest_created_str = oldest_issues[0].get("fields", {}).get("created", "")

    # Setup pass 2: newest issue date
    try:
        newest_data = jira.search_issues_post(
            f'{base_jql} ORDER BY created DESC', max_results=1,
            fields=[team_field_id, "created"],
        )
    except JiraError as exc:
        logger.warning("PowerAgent._discover_teams: newest-issue query failed (%s)", exc)
        return []
    newest_issues = newest_data.get("issues", [])
    if not newest_issues:
        return []
    newest_created_str = newest_issues[0].get("fields", {}).get("created", "")

    def _parse_jira_date(s: str) -> datetime:
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            return datetime.utcnow()

    oldest_date = _parse_jira_date(oldest_created_str)
    newest_date = _parse_jira_date(newest_created_str)
    total_days = max((newest_date - oldest_date).days, 1)

    if total_issues <= 200:    num_windows = 1
    elif total_issues <= 1000: num_windows = 3
    elif total_issues <= 5000: num_windows = 5
    else:                      num_windows = 8

    results_per_window = max(50, min(100, 600 // num_windows))
    window_days = total_days / num_windows

    logger.info(
        "PowerAgent._discover_teams: %d issues, %d-day span → %d windows × %d results each",
        total_issues, total_days, num_windows, results_per_window,
    )

    seen_team_ids: set[str] = set()
    hint_teams: list[dict[str, str]] = []

    for i in range(num_windows):
        win_start = oldest_date + timedelta(days=i * window_days)
        win_end   = oldest_date + timedelta(days=(i + 1) * window_days)
        window_jql = (
            f'{base_jql} AND created >= "{win_start.strftime("%Y-%m-%d")}"'
            f' AND created <= "{win_end.strftime("%Y-%m-%d")}" ORDER BY created ASC'
        )
        try:
            data = jira.search_issues_post(window_jql, max_results=results_per_window, fields=[team_field_id])
        except JiraError as exc:
            logger.warning("PowerAgent._discover_teams: window %d failed (%s); skipping", i, exc)
            continue
        for issue in data.get("issues", []):
            fval = issue.get("fields", {}).get(team_field_id)
            if not isinstance(fval, dict):
                continue
            tid   = str(fval.get("id", ""))
            tname = str(fval.get("name", ""))
            if tid and tname and tid not in seen_team_ids:
                seen_team_ids.add(tid)
                hint_teams.append({"name": tname, "id": tid})

    logger.info("PowerAgent._discover_teams: found %d unique team(s)", len(hint_teams))
    return hint_teams


def _normalize_project_ctx(
    project_key: str,
    project: dict,
    proj_statuses: list[dict],
    proj_perms: dict,
    create_meta: dict,
    all_fields: list[dict],
    sample: dict,
    fallback_issue_types: list[str] | None = None,
    semantic_field_aliases: dict[str, str] | None = None,
    hint_teams: list[dict[str, str]] | None = None,
) -> JiraProjectContext:
    """Normalize raw Jira discovery responses into a compact JiraProjectContext."""
    # Basic project info
    project_name = project.get("name", project_key)
    project_id = str(project.get("id", ""))

    # Project statuses — flatten across issue types, deduplicate, preserve order
    status_names: list[str] = []
    seen_statuses: set[str] = set()
    for it in proj_statuses:
        for s in it.get("statuses", []):
            name = s.get("name", "")
            if name and name not in seen_statuses:
                seen_statuses.add(name)
                status_names.append(name)

    # Permissions
    can_create = _perm_flag(proj_perms, "CREATE_ISSUES")
    can_browse = _perm_flag(proj_perms, "BROWSE_PROJECTS")

    # Build a name->id map for all global fields (used for lookup)
    global_field_map: dict[str, dict] = {f["id"]: f for f in all_fields}

    # Issue types + required fields from createmeta
    issue_types: list[str] = []
    required_create_fields: dict[str, dict[str, str]] = {}
    createmeta_field_ids: set[str] = set()

    projects_meta = create_meta.get("projects", [])
    if projects_meta:
        for issuetype in projects_meta[0].get("issuetypes", []):
            it_name = issuetype.get("name", "")
            if it_name and it_name not in issue_types:
                issue_types.append(it_name)
            fields_meta = issuetype.get("fields", {})
            required: dict[str, str] = {}
            for fid, fmeta in fields_meta.items():
                createmeta_field_ids.add(fid)
                if fmeta.get("required", False):
                    fname = fmeta.get("name", fid)
                    required[fid] = fname
            if required and it_name:
                required_create_fields[it_name] = required
    else:
        logger.warning("PowerAgent: createmeta returned no projects for key=%s", project_key)
        if fallback_issue_types:
            issue_types = fallback_issue_types

    # Field mappings — only fields from createmeta + fields observed in sample
    sample_field_ids: set[str] = set()
    for issue in sample.get("issues", []):
        sample_field_ids.update(issue.get("fields", {}).keys())

    relevant_field_ids = createmeta_field_ids | sample_field_ids
    field_mappings: list[JiraFieldInfo] = []
    seen_field_ids: set[str] = set()
    for fid in sorted(relevant_field_ids):
        if fid in seen_field_ids:
            continue
        seen_field_ids.add(fid)
        meta = global_field_map.get(fid)
        if meta:
            field_mappings.append(JiraFieldInfo(
                field_id=fid,
                name=meta.get("name", fid),
                custom=meta.get("custom", False),
            ))

    # Heuristic hints from sample issues
    hint_labels: list[str] = []
    hint_components: list[str] = []
    seen_labels: set[str] = set()
    seen_components: set[str] = set()
    for issue in sample.get("issues", []):
        fields = issue.get("fields", {})
        for label in fields.get("labels", []) or []:
            if label and label not in seen_labels:
                seen_labels.add(label)
                hint_labels.append(label)
        for comp in fields.get("components", []) or []:
            name = comp.get("name", "") if isinstance(comp, dict) else str(comp)
            if name and name not in seen_components:
                seen_components.add(name)
                hint_components.append(name)

    return JiraProjectContext(
        project_key=project_key,
        project_name=project_name,
        project_id=project_id,
        issue_types=issue_types,
        statuses=status_names,
        field_mappings=field_mappings,
        required_create_fields=required_create_fields,
        can_create_issues=can_create,
        can_browse=can_browse,
        hint_labels=hint_labels,
        hint_components=hint_components,
        semantic_field_aliases=semantic_field_aliases or {},
        hint_teams=hint_teams or [],
        discovered_at=datetime.now(timezone.utc).isoformat(),
    )


def _build_context_summary(global_ctx: dict, project_ctx: JiraProjectContext | None) -> str:
    """Build a concise context string for injection into the agent prompt."""
    lines: list[str] = []

    # Global info
    lines.append(
        f"Jira: {global_ctx.get('server_url', '?')} (v{global_ctx.get('server_version', '?')}) "
        f"| User: {global_ctx.get('current_user', '?')} ({global_ctx.get('current_user_id', '?')})"
    )
    lines.append(f"Priorities: {', '.join(global_ctx.get('priorities', []))}")
    lines.append(f"Resolutions: {', '.join(global_ctx.get('resolutions', []))}")

    if project_ctx is None:
        lines.append("(No project context — run project discovery first)")
        return "\n".join(lines)

    lines.append(f"Project: {project_ctx.project_key} — {project_ctx.project_name}")
    lines.append(f"Issue types: {', '.join(project_ctx.issue_types)}")
    lines.append(f"Statuses: {', '.join(project_ctx.statuses)}")

    # Custom fields — show up to MAX_SUMMARY_CUSTOM_FIELDS (raised to 30)
    custom_fields = [f for f in project_ctx.field_mappings if f.custom][:MAX_SUMMARY_CUSTOM_FIELDS]
    if custom_fields:
        cf_parts = [f"{f.name} ({f.field_id})" for f in custom_fields]
        lines.append(f"Custom fields ({len(cf_parts)}): {', '.join(cf_parts)}")

    # Semantic field aliases — map natural-language concepts to field IDs
    if project_ctx.semantic_field_aliases:
        alias_parts = [f"{alias} → {fid}" for alias, fid in project_ctx.semantic_field_aliases.items()]
        lines.append(f"Semantic field aliases (use these instead of guessing): {', '.join(alias_parts)}")

    # Team name→ID map — team names do not work in JQL, IDs required
    if project_ctx.hint_teams:
        team_parts = [f"{t['name']} (id={t['id']})" for t in project_ctx.hint_teams]
        lines.append(f"Known team IDs (team names do NOT work in JQL — use IDs): {', '.join(team_parts)}")

    # Required create fields — show readable names
    if project_ctx.required_create_fields:
        req_parts = []
        for it_name, fields in list(project_ctx.required_create_fields.items())[:5]:
            names = ", ".join(fields.values())
            req_parts.append(f"{it_name}: {names}")
        lines.append(f"Required fields — {' | '.join(req_parts)}")

    # Permissions
    lines.append(
        f"Permissions: CREATE_ISSUES={str(project_ctx.can_create_issues).lower()}, "
        f"BROWSE_PROJECTS={str(project_ctx.can_browse).lower()}"
    )

    # Heuristic hints — capped
    if project_ctx.hint_labels:
        lines.append(f"Hint labels (from recent issues): {', '.join(project_ctx.hint_labels[:MAX_SUMMARY_HINTS])}")
    if project_ctx.hint_components:
        lines.append(f"Hint components (from recent issues): {', '.join(project_ctx.hint_components[:MAX_SUMMARY_HINTS])}")

    return "\n".join(lines)


def _summarize_results(issues: list[dict], total: int) -> str:
    """Compact issue summary for LLM observation messages. Max 10 issues shown."""
    if not issues:
        return "No issues found."
    shown = issues[:10]
    lines = [f"{total} issues found. Top {len(shown)}:"]
    for issue in shown:
        fields = issue.get("fields", {})
        key = issue.get("key", "?")
        summary = (fields.get("summary") or "")[:60]
        status = (fields.get("status") or {}).get("name", "?")
        assignee_field = fields.get("assignee")
        assignee = assignee_field.get("displayName", "Unassigned") if assignee_field else "Unassigned"
        lines.append(f"  {key}: {summary} ({status}, {assignee})")
    return "\n".join(lines)


def _ensure_project_scope(jql: str, project_key: str) -> str:
    """Prepend project scope if the JQL doesn't already include one."""
    if not project_key or re.search(r"\bproject\s*[=!]", jql, re.IGNORECASE):
        return jql
    logger.warning(
        "PowerAgent: JQL missing project scope, prepending project = %r. Original JQL: %s",
        project_key,
        jql,
    )
    return f'project = "{project_key}" AND {jql}'


# ---------------------------------------------------------------------------
# PowerAgent
# ---------------------------------------------------------------------------


class PowerAgent:
    def __init__(
        self,
        llm: LLMClient,
        prompt_loader: PromptLoader,
        jira: JiraClient,
        store: DocumentStore,
    ) -> None:
        self._llm = llm
        self._pl = prompt_loader
        self._jira = jira
        self._store = store

    # ------------------------------------------------------------------
    # Stage 1 — global bootstrap (transient, per run() call)
    # ------------------------------------------------------------------

    def bootstrap_global(self) -> dict:
        """Run 8 lightweight global API calls. Returns in-memory dict, not stored."""
        myself = self._jira.get_myself()
        server = self._jira.get_server_info()
        fields = self._jira.get_fields()
        perms = self._jira.get_my_permissions()
        issue_types = self._jira.get_issue_types()
        priorities = self._jira.get_priorities()
        statuses = self._jira.get_statuses()
        resolutions = self._jira.get_resolutions()
        return {
            "server_url": server.get("baseUrl", ""),
            "server_version": server.get("version", ""),
            "current_user": myself.get("displayName", ""),
            "current_user_id": myself.get("accountId", myself.get("name", "")),
            "global_issue_types": [it["name"] for it in issue_types],
            "priorities": [p["name"] for p in priorities],
            "global_statuses": [s["name"] for s in statuses],
            "resolutions": [r["name"] for r in resolutions],
            "field_map": {f["name"]: f["id"] for f in fields},
            "can_create_globally": _perm_flag(perms, "CREATE_ISSUES"),
        }

    # ------------------------------------------------------------------
    # Stage 2 — project-scoped discovery (persistent, stored in workspace)
    # ------------------------------------------------------------------

    def discover_project(self, session_id: str) -> PowerDiscoverResponse:
        """Run 6 project-scoped API calls. Normalize and store in workspace.jira_project_ctx."""
        pk = self._jira._settings.jira_project_key

        project = self._jira.get_project(pk)
        proj_statuses = self._jira.get_project_statuses(pk)
        proj_perms = self._jira.get_my_permissions(project_key=pk)
        fallback_issue_types: list[str] | None = None
        try:
            create_meta = self._jira.get_create_meta(pk)
        except JiraError as exc:
            logger.warning("PowerAgent: createmeta unavailable (%s); falling back to /issuetype", exc)
            create_meta = {}
            try:
                fallback_issue_types = [it["name"] for it in self._jira.get_issue_types() if it.get("name")]
            except JiraError as exc2:
                logger.warning("PowerAgent: get_issue_types also failed (%s); issue types will be empty", exc2)
        all_fields = self._jira.get_fields()

        # Detect semantic aliases and team field before sample fetch
        semantic_field_aliases = _detect_semantic_aliases(all_fields)
        team_field_id = _detect_team_field_id(all_fields)
        if semantic_field_aliases:
            logger.info("PowerAgent: detected semantic aliases: %s", list(semantic_field_aliases.keys()))

        sample = self._jira.search_issues_post(
            f'project = "{pk}" ORDER BY updated DESC',
            max_results=10,
            fields=["issuetype", "status", "priority", "labels", "components", "assignee", "reporter"],
        )

        # Adaptive time-windowed team discovery
        hint_teams: list[dict[str, str]] = []
        if team_field_id:
            hint_teams = _discover_teams(self._jira, pk, team_field_id)

        ctx = _normalize_project_ctx(
            pk, project, proj_statuses, proj_perms, create_meta, all_fields, sample,
            fallback_issue_types, semantic_field_aliases, hint_teams,
        )

        ws = self._store.load_workspace(session_id)
        ws.jira_project_ctx = ctx.model_dump()
        self._store.save_workspace(ws)

        logger.info(
            "PowerAgent: project discovery complete for %s — %d issue types, %d statuses, %d field mappings",
            pk,
            len(ctx.issue_types),
            len(ctx.statuses),
            len(ctx.field_mappings),
        )

        return PowerDiscoverResponse(
            session_id=session_id,
            project_key=ctx.project_key,
            project_name=ctx.project_name,
            issue_types=ctx.issue_types,
            statuses=ctx.statuses,
            custom_field_count=sum(1 for f in ctx.field_mappings if f.custom),
            can_create_issues=ctx.can_create_issues,
        )

    # ------------------------------------------------------------------
    # Agent loop
    # ------------------------------------------------------------------

    def run(self, session_id: str, goal: str) -> Generator[PowerStepEvent, None, None]:
        """Agentic plan/action/observe loop. Yields PowerStepEvent instances."""
        if not self._jira.is_configured():
            yield PowerStepEvent(
                type="error",
                content="Jira is not configured. Set JIRA_BASE_URL, JIRA_USER, JIRA_API_TOKEN, and JIRA_PROJECT_KEY in .env.",
            )
            yield PowerStepEvent(type="done", content="")
            return

        # Stage 1: global bootstrap (transient)
        try:
            global_ctx = self.bootstrap_global()
        except JiraError as exc:
            yield PowerStepEvent(type="error", content=f"Global bootstrap failed: {exc}")
            yield PowerStepEvent(type="done", content="")
            return

        # Load stored project context (stage 2)
        ws = self._store.load_workspace(session_id)
        project_ctx: JiraProjectContext | None = None
        if ws.jira_project_ctx:
            try:
                project_ctx = JiraProjectContext.model_validate(ws.jira_project_ctx)
            except Exception as exc:
                logger.warning("PowerAgent: failed to parse jira_project_ctx: %s", exc)
        else:
            logger.warning("PowerAgent: no project context for session %s — run discovery first", session_id)

        pk = self._jira._settings.jira_project_key or ""
        summary = _build_context_summary(global_ctx, project_ctx)
        system = self._pl.load_prompt("power_jira_agent.md", jira_env=summary)

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Goal: {goal}"},
        ]

        for iteration in range(MAX_ITERATIONS):
            logger.info("PowerAgent iteration %d/%d session=%s", iteration + 1, MAX_ITERATIONS, session_id)

            try:
                response = self._llm.chat_json(messages, temperature=0.1, max_tokens=POWER_MAX_TOKENS)
            except LLMError as exc:
                yield PowerStepEvent(type="error", content=f"LLM error: {exc}")
                yield PowerStepEvent(type="done", content="")
                return

            thought = str(response.get("thought", ""))
            action = str(response.get("action", ""))
            is_done = bool(response.get("done", False))

            if action == "synthesize" or is_done:
                report = str(response.get("report", thought or "No report generated."))
                yield PowerStepEvent(type="result", content=report)
                yield PowerStepEvent(type="done", content="")
                return

            if action == "search_jira":
                raw_jql = str(response.get("jql", "")).strip()
                if not raw_jql:
                    yield PowerStepEvent(type="error", content="Agent produced an empty JQL. Stopping.")
                    yield PowerStepEvent(type="done", content="")
                    return

                jql = _ensure_project_scope(raw_jql, pk)
                event_type = "plan" if iteration == 0 else "replan"
                yield PowerStepEvent(type=event_type, content=thought or f"Running: {jql}", jql=jql)
                yield PowerStepEvent(type="action", content=f"Querying Jira: {jql}", jql=jql)

                try:
                    data = self._jira.search_issues_post(jql, max_results=50)
                except JiraError as exc:
                    observation = f"Jira query failed: {exc}"
                    yield PowerStepEvent(type="observe", content=observation, jql=jql, result_count=0)
                    messages.append({"role": "assistant", "content": json.dumps(response)})
                    messages.append({"role": "user", "content": f"Observation: {observation}"})
                    continue

                issues = data.get("issues", [])
                total = data.get("total", len(issues))  # v3 /search/jql omits total; fall back to len
                obs_text = _summarize_results(issues, total)
                yield PowerStepEvent(type="observe", content=obs_text, jql=jql, result_count=total)

                messages.append({"role": "assistant", "content": json.dumps(response)})
                messages.append({
                    "role": "user",
                    "content": f"Observation: {total} issues found.\n{obs_text}",
                })

            else:
                yield PowerStepEvent(type="error", content=f"Unknown action '{action}'. Stopping.")
                yield PowerStepEvent(type="done", content="")
                return

        # Max iterations reached — force synthesis
        logger.warning("PowerAgent: max iterations (%d) reached for session %s", MAX_ITERATIONS, session_id)
        messages.append({
            "role": "user",
            "content": (
                "You have reached the maximum number of queries. "
                "Synthesize your findings into a final report now. "
                "Respond with action=synthesize and done=true."
            ),
        })
        try:
            final = self._llm.chat_json(messages, temperature=0.1, max_tokens=POWER_MAX_TOKENS)
            report = str(final.get("report", "Maximum iterations reached. Partial analysis only."))
        except LLMError as exc:
            report = f"Could not synthesize after max iterations: {exc}"

        yield PowerStepEvent(type="result", content=report)
        yield PowerStepEvent(type="done", content="")


def get_power_agent() -> PowerAgent:
    from backend.app.services.document_store import get_document_store
    from backend.app.services.jira_client import get_jira_client
    from backend.app.services.llm_client import get_llm_client
    from backend.app.services.prompt_loader import get_prompt_loader

    return PowerAgent(
        llm=get_llm_client(),
        prompt_loader=get_prompt_loader(),
        jira=get_jira_client(),
        store=get_document_store(),
    )
