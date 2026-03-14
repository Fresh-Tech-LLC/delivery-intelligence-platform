"""
Tests for the Model Capability Probe feature.

Covers:
1. Model serialization roundtrips
2. Report grading logic (multiple scenarios)
3. ProbeStore file I/O (uses tmp_path)
4. Route tests via FastAPI TestClient (mock probe service)
5. ProbeLLMClient behaviour (mocked httpx)
6. Honest step assessment logic (structured_json_output, tool_call_readiness)

Run with:
    cd backend
    python -m pytest tests/test_capability_probe.py -v
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from backend.app.models.capability_probe import (
    CapabilityAssessment,
    CapabilityProbeReport,
    CapabilityProbeRun,
    CapabilityProbeStepResult,
    FinalRecommendation,
    ProbeStatus,
    StepStatus,
)
from backend.app.services.capability_probe.probe_runner import PROBE_STEPS, grade_report
from backend.app.services.capability_probe.probe_store import ProbeStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_A = CapabilityAssessment
_P = CapabilityAssessment.pass_
_W = CapabilityAssessment.warning
_F = CapabilityAssessment.fail
_U = CapabilityAssessment.unknown


def _make_step(name: str, assessment: CapabilityAssessment, status: StepStatus = StepStatus.passed) -> CapabilityProbeStepResult:
    return CapabilityProbeStepResult(
        name=name,
        status=status,
        assessment=assessment,
        summary="test",
        duration_ms=100.0,
    )


def _make_steps(overrides: dict[str, CapabilityAssessment]) -> list[CapabilityProbeStepResult]:
    """Build a full step list using overrides dict, defaulting remaining to pass."""
    defaults = {n: _P for n in PROBE_STEPS}
    defaults.update(overrides)
    return [_make_step(n, a) for n, a in defaults.items()]


def _make_run(run_id: str = "abc123") -> CapabilityProbeRun:
    return CapabilityProbeRun(
        run_id=run_id,
        status=ProbeStatus.completed,
        started_at="2025-01-01T00:00:00+00:00",
        total_steps=len(PROBE_STEPS),
    )


def _make_report(run_id: str = "abc123") -> CapabilityProbeReport:
    steps = _make_steps({})
    return grade_report(run_id, steps, {"model_name": "test-model"})


# ---------------------------------------------------------------------------
# 1. Model serialization roundtrips
# ---------------------------------------------------------------------------


class TestModelSerialization:
    def test_step_result_roundtrip(self):
        step = CapabilityProbeStepResult(
            name="connectivity_check",
            status=StepStatus.passed,
            assessment=CapabilityAssessment.pass_,
            summary="OK",
            duration_ms=42.5,
            details={"latency_ms": 42.5},
        )
        data = step.model_dump(mode="json")
        restored = CapabilityProbeStepResult.model_validate(data)
        assert restored.name == step.name
        assert restored.assessment == step.assessment
        assert restored.duration_ms == step.duration_ms

    def test_probe_run_roundtrip(self):
        run = _make_run()
        run.steps = _make_steps({})
        data = run.model_dump(mode="json")
        restored = CapabilityProbeRun.model_validate(data)
        assert restored.run_id == run.run_id
        assert restored.total_steps == run.total_steps
        assert len(restored.steps) == len(run.steps)

    def test_report_roundtrip(self):
        report = _make_report()
        data = report.model_dump(mode="json")
        restored = CapabilityProbeReport.model_validate(data)
        assert restored.run_id == report.run_id
        assert restored.recommendation == report.recommendation

    def test_assessment_enum_values(self):
        assert CapabilityAssessment.pass_.value == "pass"
        assert CapabilityAssessment.warning.value == "warning"
        assert CapabilityAssessment.fail.value == "fail"
        assert CapabilityAssessment.unknown.value == "unknown"

    def test_final_recommendation_values(self):
        assert FinalRecommendation.suitable.value == "suitable for basic agentic workflows"
        assert FinalRecommendation.usable.value == "usable with limitations"
        assert FinalRecommendation.not_suitable.value == "not yet suitable"


# ---------------------------------------------------------------------------
# 2. Grading logic
# ---------------------------------------------------------------------------


class TestGrading:
    def test_grade_suitable_all_pass(self):
        steps = _make_steps({})
        report = grade_report("r1", steps, {})
        assert report.recommendation == FinalRecommendation.suitable

    def test_grade_suitable_tool_unknown(self):
        """tool_call_readiness=unknown should not block 'suitable'."""
        steps = _make_steps({"tool_call_readiness": _U})
        report = grade_report("r1", steps, {})
        assert report.recommendation == FinalRecommendation.suitable

    def test_grade_suitable_ctx_warning(self):
        """long_context_smoke=warning should still allow 'suitable'."""
        steps = _make_steps({"long_context_smoke": _W})
        report = grade_report("r1", steps, {})
        assert report.recommendation == FinalRecommendation.suitable

    def test_grade_suitable_deterministic_warning(self):
        """deterministic_generation=warning does NOT block suitable."""
        steps = _make_steps({"deterministic_generation": _W})
        report = grade_report("r1", steps, {})
        assert report.recommendation == FinalRecommendation.suitable

    def test_grade_not_suitable_gen_fail(self):
        steps = _make_steps({"simple_generation": _F})
        report = grade_report("r1", steps, {})
        assert report.recommendation == FinalRecommendation.not_suitable

    def test_grade_not_suitable_json_fail(self):
        steps = _make_steps({"structured_json_output": _F})
        report = grade_report("r1", steps, {})
        assert report.recommendation == FinalRecommendation.not_suitable

    def test_grade_not_suitable_conn_fail(self):
        steps = _make_steps({"connectivity_check": _F})
        report = grade_report("r1", steps, {})
        assert report.recommendation == FinalRecommendation.not_suitable

    def test_grade_usable_ctx_fail(self):
        """Context failure with gen+json passing → usable."""
        steps = _make_steps({"long_context_smoke": _F})
        report = grade_report("r1", steps, {})
        assert report.recommendation == FinalRecommendation.usable

    def test_grade_usable_json_warning(self):
        """JSON warning → usable (not 'pass' so not suitable, not 'fail' so not not_suitable)."""
        steps = _make_steps({"structured_json_output": _W})
        report = grade_report("r1", steps, {})
        assert report.recommendation == FinalRecommendation.usable

    def test_grade_report_includes_all_steps(self):
        steps = _make_steps({})
        report = grade_report("r1", steps, {})
        assert set(report.per_capability.keys()) == set(PROBE_STEPS)

    def test_grade_report_includes_timings(self):
        steps = _make_steps({})
        report = grade_report("r1", steps, {})
        for name in PROBE_STEPS:
            assert name in report.timings

    def test_grade_report_meta_propagated(self):
        meta = {"model_name": "gpt-x", "probe_context_smoke_size": 40}
        steps = _make_steps({})
        report = grade_report("r1", steps, meta)
        assert report.probe_meta == meta


# ---------------------------------------------------------------------------
# 3. ProbeStore file I/O
# ---------------------------------------------------------------------------


class TestProbeStore:
    def _store(self, tmp_path: Path) -> ProbeStore:
        return ProbeStore(base_dir=tmp_path / "capability_probes")

    def test_save_and_get_run(self, tmp_path: Path):
        store = self._store(tmp_path)
        run = _make_run("run1")
        store.save_run(run)
        loaded = store.get_run("run1")
        assert loaded is not None
        assert loaded.run_id == "run1"
        assert loaded.status == ProbeStatus.completed

    def test_get_run_missing_returns_none(self, tmp_path: Path):
        store = self._store(tmp_path)
        assert store.get_run("doesnotexist") is None

    def test_save_and_get_steps(self, tmp_path: Path):
        store = self._store(tmp_path)
        steps = _make_steps({})
        store.save_steps("run1", steps)
        loaded = store.get_steps("run1")
        assert len(loaded) == len(PROBE_STEPS)
        assert loaded[0].name == PROBE_STEPS[0]

    def test_get_steps_missing_returns_empty(self, tmp_path: Path):
        store = self._store(tmp_path)
        assert store.get_steps("norun") == []

    def test_save_and_get_report(self, tmp_path: Path):
        store = self._store(tmp_path)
        report = _make_report("run1")
        store.save_report(report)
        loaded = store.get_report("run1")
        assert loaded is not None
        assert loaded.run_id == "run1"
        assert isinstance(loaded.recommendation, FinalRecommendation)

    def test_get_report_missing_returns_none(self, tmp_path: Path):
        store = self._store(tmp_path)
        assert store.get_report("norun") is None

    def test_list_runs_sorted_by_started_at(self, tmp_path: Path):
        store = self._store(tmp_path)
        run_a = CapabilityProbeRun(
            run_id="aaa",
            status=ProbeStatus.completed,
            started_at="2025-01-01T10:00:00+00:00",
            total_steps=6,
        )
        run_b = CapabilityProbeRun(
            run_id="bbb",
            status=ProbeStatus.completed,
            started_at="2025-01-02T10:00:00+00:00",
            total_steps=6,
        )
        store.save_run(run_a)
        store.save_run(run_b)
        runs = store.list_runs(limit=10)
        assert runs[0].run_id == "bbb"  # newer first
        assert runs[1].run_id == "aaa"

    def test_list_runs_limit(self, tmp_path: Path):
        store = self._store(tmp_path)
        for i in range(5):
            run = CapabilityProbeRun(
                run_id=f"run{i}",
                status=ProbeStatus.completed,
                started_at=f"2025-01-0{i+1}T00:00:00+00:00",
                total_steps=6,
            )
            store.save_run(run)
        assert len(store.list_runs(limit=3)) == 3

    def test_list_runs_empty_dir(self, tmp_path: Path):
        store = self._store(tmp_path)
        assert store.list_runs() == []

    def test_atomic_write_overwrites(self, tmp_path: Path):
        """Saving the same run twice should update in place."""
        store = self._store(tmp_path)
        run = _make_run("run1")
        store.save_run(run)
        run.status = ProbeStatus.failed
        run.error = "test error"
        store.save_run(run)
        loaded = store.get_run("run1")
        assert loaded.status == ProbeStatus.failed
        assert loaded.error == "test error"


# ---------------------------------------------------------------------------
# 4. Route tests
# ---------------------------------------------------------------------------


class TestRoutes:
    """Route tests using FastAPI TestClient with a mocked ProbeService."""

    @pytest.fixture
    def client(self, tmp_path: Path):
        from fastapi.testclient import TestClient
        from backend.app.main import app

        mock_svc = MagicMock()
        mock_svc.list_runs.return_value = []
        mock_svc.active_run_id.return_value = None

        run = _make_run("testrun1")
        mock_svc.create_run.return_value = run
        mock_svc.get_run.return_value = run
        mock_svc.get_run_status.return_value = {
            "run_id": "testrun1",
            "status": "completed",
            "completed_steps": 6,
            "total_steps": 6,
            "current_step": None,
            "finished_at": "2025-01-01T01:00:00+00:00",
            "error": None,
            "steps": [],
        }
        mock_svc.get_report.return_value = None
        mock_svc._store = MagicMock()
        mock_svc._store.get_steps.return_value = []

        with patch(
            "backend.app.routers.admin_probe.get_probe_service",
            return_value=mock_svc,
        ):
            with patch(
                "backend.app.services.capability_probe.probe_service.get_probe_service",
                return_value=mock_svc,
            ):
                yield TestClient(app, raise_server_exceptions=True), mock_svc

    def test_index_page_200(self, client):
        tc, _ = client
        resp = tc.get("/admin/probe")
        assert resp.status_code == 200
        assert "Model Capability Probe" in resp.text

    def test_index_shows_error_already_running(self, client):
        tc, _ = client
        resp = tc.get("/admin/probe?error=already_running")
        assert resp.status_code == 200
        assert "already in progress" in resp.text

    def test_start_run_redirects_to_run_page(self, client):
        tc, mock_svc = client
        resp = tc.post("/admin/probe/runs", follow_redirects=False)
        assert resp.status_code == 303
        assert "/admin/probe/runs/testrun1" in resp.headers["location"]
        mock_svc.create_run.assert_called_once()
        mock_svc.start_run.assert_called_once_with("testrun1")

    def test_start_run_already_running_redirects_with_error(self, client):
        tc, mock_svc = client
        mock_svc.start_run.side_effect = ValueError("already running")
        resp = tc.post("/admin/probe/runs", follow_redirects=False)
        assert resp.status_code == 303
        assert "already_running" in resp.headers["location"]

    def test_run_detail_page_200(self, client):
        tc, _ = client
        resp = tc.get("/admin/probe/runs/testrun1")
        assert resp.status_code == 200
        assert "testrun1" in resp.text

    def test_run_detail_page_404(self, client):
        tc, mock_svc = client
        mock_svc.get_run.return_value = None
        resp = tc.get("/admin/probe/runs/doesnotexist")
        assert resp.status_code == 404

    def test_status_endpoint_returns_json(self, client):
        tc, _ = client
        resp = tc.get("/admin/probe/runs/testrun1/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == "testrun1"
        assert data["status"] == "completed"
        assert "completed_steps" in data
        assert "steps" in data


# ---------------------------------------------------------------------------
# 5. ProbeLLMClient behaviour
# ---------------------------------------------------------------------------


def _ok_response(content: str | None = "OK", tool_calls=None) -> dict:
    """Build a minimal OpenAI-compatible 200 response body."""
    return {
        "choices": [
            {
                "message": {
                    "content": content,
                    "tool_calls": tool_calls,
                }
            }
        ]
    }


class TestProbeLLMClient:
    """Unit tests for ProbeLLMClient using mocked httpx."""

    @pytest.fixture
    def client(self):
        from backend.app.services.capability_probe.probe_llm_client import ProbeLLMClient
        settings = MagicMock()
        settings.llm_api_base = "https://fake.llm/v1"
        settings.llm_access_key = None
        settings.llm_secret_key = None
        settings.llm_api_key = "test-key"
        settings.llm_model_name = "test-model"
        settings.llm_max_tokens_param = "max_tokens"
        settings.probe_step_timeout = 30
        settings.llm_ca_bundle = None
        return ProbeLLMClient(settings)

    def _mock_resp(self, status_code: int, body: dict | None = None, text: str = ""):
        resp = MagicMock()
        resp.status_code = status_code
        if body is not None:
            resp.json.return_value = body
        else:
            resp.json.side_effect = Exception("not json")
        resp.text = text
        return resp

    def test_returns_content_on_200(self, client):
        resp = self._mock_resp(200, _ok_response("Hello"))
        with patch("httpx.Client") as mock_cls:
            mock_cls.return_value.__enter__.return_value.post.return_value = resp
            result = client.send([{"role": "user", "content": "hi"}])
        assert result.content == "Hello"
        assert result.http_status == 200
        assert result.error is None
        assert result.ok is True

    def test_returns_none_content_when_null(self, client):
        """content=null is valid for tool-call-only responses."""
        resp = self._mock_resp(200, _ok_response(content=None, tool_calls=[{"type": "function"}]))
        with patch("httpx.Client") as mock_cls:
            mock_cls.return_value.__enter__.return_value.post.return_value = resp
            result = client.send([{"role": "user", "content": "hi"}])
        assert result.content is None
        assert result.tool_calls is not None
        assert result.error is None

    def test_returns_tool_calls_when_present(self, client):
        tool_calls = [{"type": "function", "function": {"name": "get_weather", "arguments": "{}"}}]
        resp = self._mock_resp(200, _ok_response(content=None, tool_calls=tool_calls))
        with patch("httpx.Client") as mock_cls:
            mock_cls.return_value.__enter__.return_value.post.return_value = resp
            result = client.send([{"role": "user", "content": "weather?"}], tools=[{}])
        assert result.tool_calls == tool_calls

    def test_returns_error_on_400(self, client):
        resp = self._mock_resp(400, {"error": {"message": "bad request"}})
        with patch("httpx.Client") as mock_cls:
            mock_cls.return_value.__enter__.return_value.post.return_value = resp
            result = client.send([{"role": "user", "content": "hi"}])
        assert result.http_status == 400
        assert result.error == "bad request"
        assert result.content is None
        assert result.ok is False

    def test_returns_error_on_timeout(self, client):
        import httpx
        with patch("httpx.Client") as mock_cls:
            mock_cls.return_value.__enter__.return_value.post.side_effect = httpx.TimeoutException("timed out")
            result = client.send([{"role": "user", "content": "hi"}])
        assert result.http_status is None
        assert result.error is not None
        assert "timed out" in result.error.lower() or "timeout" in result.error.lower()
        assert result.ok is False

    def test_payload_sent_excludes_auth(self, client):
        resp = self._mock_resp(200, _ok_response("hi"))
        with patch("httpx.Client") as mock_cls:
            mock_cls.return_value.__enter__.return_value.post.return_value = resp
            result = client.send([{"role": "user", "content": "hi"}])
        assert "Authorization" not in result.payload_sent
        assert "model" in result.payload_sent

    def test_no_retries_on_5xx(self, client):
        resp = self._mock_resp(503, {"error": {"message": "service unavailable"}})
        with patch("httpx.Client") as mock_cls:
            mock_inst = mock_cls.return_value.__enter__.return_value
            mock_inst.post.return_value = resp
            result = client.send([{"role": "user", "content": "hi"}])
        assert mock_inst.post.call_count == 1
        assert result.http_status == 503

    def test_json_mode_adds_response_format(self, client):
        resp = self._mock_resp(200, _ok_response('{"a": 1}'))
        with patch("httpx.Client") as mock_cls:
            mock_cls.return_value.__enter__.return_value.post.return_value = resp
            result = client.send([{"role": "user", "content": "hi"}], json_mode=True)
        assert result.payload_sent.get("response_format") == {"type": "json_object"}

    def test_tools_adds_tool_choice(self, client):
        resp = self._mock_resp(200, _ok_response())
        with patch("httpx.Client") as mock_cls:
            mock_cls.return_value.__enter__.return_value.post.return_value = resp
            result = client.send([{"role": "user", "content": "hi"}], tools=[{"type": "function"}])
        assert "tool_choice" in result.payload_sent
        assert result.payload_sent["tool_choice"] == "auto"

    def test_temperature_always_sent(self, client):
        """Temperature must be included unconditionally — no settings gate."""
        resp = self._mock_resp(200, _ok_response())
        with patch("httpx.Client") as mock_cls:
            mock_cls.return_value.__enter__.return_value.post.return_value = resp
            result = client.send([{"role": "user", "content": "hi"}], temperature=0.7)
        assert result.payload_sent.get("temperature") == 0.7

    def test_seed_not_included(self, client):
        """llm_seed must never be sent by ProbeLLMClient."""
        resp = self._mock_resp(200, _ok_response())
        with patch("httpx.Client") as mock_cls:
            mock_cls.return_value.__enter__.return_value.post.return_value = resp
            result = client.send([{"role": "user", "content": "hi"}])
        assert "seed" not in result.payload_sent


# ---------------------------------------------------------------------------
# 6. Honest step assessment logic
# ---------------------------------------------------------------------------


def _make_probe_result(
    content: str | None = None,
    tool_calls=None,
    raw_response=None,
    http_status: int | None = 200,
    error: str | None = None,
    latency_ms: float = 50.0,
):
    from backend.app.services.capability_probe.probe_llm_client import ProbeCallResult
    return ProbeCallResult(
        content=content,
        tool_calls=tool_calls,
        raw_response=raw_response,
        http_status=http_status,
        error=error,
        latency_ms=latency_ms,
    )


def _make_runner(mock_send_side_effect):
    """Return a ProbeRunner whose _probe_client.send is replaced with a mock."""
    from backend.app.services.capability_probe.probe_runner import ProbeRunner
    store = MagicMock()
    settings = MagicMock()
    settings.probe_context_smoke_size = 5
    runner = ProbeRunner(store=store, settings=settings)
    runner._probe_client = MagicMock()
    runner._probe_client.send.side_effect = mock_send_side_effect
    return runner


def _run_step(runner, step_name: str) -> CapabilityProbeStepResult:
    step = CapabilityProbeStepResult(name=step_name)
    runner._dispatch(step)
    return step


class TestStructuredJsonStep:
    def test_pass_on_clean_json(self):
        content = '{"name": "project", "items": [{"id": 1, "label": "alpha"}, {"id": 2, "label": "beta"}]}'
        runner = _make_runner(lambda *a, **kw: _make_probe_result(content=content))
        step = _run_step(runner, "structured_json_output")
        assert step.assessment == CapabilityAssessment.pass_
        assert step.details["json_mode_clean"] is True
        assert step.details["schema_valid"] is True

    def test_warning_on_fence_wrapped_json(self):
        inner = '{"name": "project", "items": [{"id": 1, "label": "a"}, {"id": 2, "label": "b"}]}'
        content = f"```json\n{inner}\n```"
        runner = _make_runner(lambda *a, **kw: _make_probe_result(content=content))
        step = _run_step(runner, "structured_json_output")
        assert step.assessment == CapabilityAssessment.warning
        assert step.details["json_mode_clean"] is False

    def test_fail_on_non_json_text(self):
        runner = _make_runner(lambda *a, **kw: _make_probe_result(content="I cannot do that."))
        step = _run_step(runner, "structured_json_output")
        assert step.assessment == CapabilityAssessment.fail

    def test_fail_on_null_content(self):
        runner = _make_runner(lambda *a, **kw: _make_probe_result(content=None))
        step = _run_step(runner, "structured_json_output")
        assert step.assessment == CapabilityAssessment.fail

    def test_fail_on_empty_content(self):
        runner = _make_runner(lambda *a, **kw: _make_probe_result(content="   "))
        step = _run_step(runner, "structured_json_output")
        assert step.assessment == CapabilityAssessment.fail

    def test_fail_on_400(self):
        runner = _make_runner(lambda *a, **kw: _make_probe_result(
            content=None, http_status=400, error="bad request"
        ))
        step = _run_step(runner, "structured_json_output")
        assert step.assessment == CapabilityAssessment.fail
        assert "400" in step.summary

    def test_warning_on_schema_mismatch_clean_json(self):
        """JSON parses cleanly but schema is wrong → warning, not pass."""
        content = '{"wrong_key": "oops"}'
        runner = _make_runner(lambda *a, **kw: _make_probe_result(content=content))
        step = _run_step(runner, "structured_json_output")
        assert step.assessment == CapabilityAssessment.warning
        assert step.details["json_mode_clean"] is True
        assert step.details["schema_valid"] is False


class TestToolCallStep:
    def test_pass_on_correct_tool_call(self):
        tool_calls = [{"function": {"name": "get_weather", "arguments": '{"location": "London"}'}}]
        runner = _make_runner(lambda *a, **kw: _make_probe_result(tool_calls=tool_calls))
        step = _run_step(runner, "tool_call_readiness")
        assert step.assessment == CapabilityAssessment.pass_
        assert step.status == StepStatus.passed

    def test_warning_on_wrong_tool_name(self):
        tool_calls = [{"function": {"name": "search_web", "arguments": "{}"}}]
        runner = _make_runner(lambda *a, **kw: _make_probe_result(tool_calls=tool_calls))
        step = _run_step(runner, "tool_call_readiness")
        assert step.assessment == CapabilityAssessment.warning

    def test_warning_when_model_replies_in_text(self):
        runner = _make_runner(lambda *a, **kw: _make_probe_result(content="The weather in London is sunny."))
        step = _run_step(runner, "tool_call_readiness")
        assert step.assessment == CapabilityAssessment.warning

    def test_unknown_on_400(self):
        runner = _make_runner(lambda *a, **kw: _make_probe_result(
            content=None, http_status=400, error="tools not supported"
        ))
        step = _run_step(runner, "tool_call_readiness")
        assert step.assessment == CapabilityAssessment.unknown
        assert step.status == StepStatus.skipped

    def test_fail_on_connection_error(self):
        runner = _make_runner(lambda *a, **kw: _make_probe_result(
            content=None, http_status=None, error="Connection refused"
        ))
        step = _run_step(runner, "tool_call_readiness")
        assert step.assessment == CapabilityAssessment.fail

    def test_fail_on_no_content_no_tool_calls(self):
        runner = _make_runner(lambda *a, **kw: _make_probe_result(content=None, http_status=200))
        step = _run_step(runner, "tool_call_readiness")
        assert step.assessment == CapabilityAssessment.fail
