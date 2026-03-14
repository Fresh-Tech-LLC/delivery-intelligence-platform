"""
Tests for the LLM adapter layer.

Covers:
1. Adapter factory selection
2. OpenAIChatAdapter normalization (mocked ProbeLLMClient)
3. ModelEngineAskAdapter normalization (mocked engine)
4. Probe step integration: feature-gated steps and adapter-driven assessment

Run with:
    cd backend
    python -m pytest tests/test_llm_adapters.py -v
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from backend.app.models.capability_probe import (
    CapabilityAssessment,
    CapabilityProbeStepResult,
    StepStatus,
)
from backend.app.services.capability_probe.probe_runner import PROBE_STEPS, ProbeRunner
from backend.app.services.llm_adapters import (
    AdapterInvocationError,
    AdapterResponse,
    AdapterUnsupportedFeatureError,
    ModelEngineAskAdapter,
    OpenAIChatAdapter,
    ToolCallResult,
    get_llm_adapter,
)
from backend.app.services.llm_adapters.base import AdapterFeatureSupport


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_settings(**overrides):
    s = MagicMock()
    s.llm_adapter_type = overrides.get("llm_adapter_type", "openai_chat")
    s.llm_model_name = overrides.get("llm_model_name", "test-model")
    s.llm_api_base = overrides.get("llm_api_base", "https://fake.llm/v1")
    s.llm_access_key = None
    s.llm_secret_key = None
    s.llm_api_key = "test-key"
    s.llm_max_tokens_param = "max_tokens"
    s.probe_step_timeout = 30
    s.llm_ca_bundle = None
    s.model_engine_default_room_id = overrides.get("model_engine_default_room_id", None)
    s.model_engine_use_history = overrides.get("model_engine_use_history", False)
    s.model_engine_default_insight_id = overrides.get("model_engine_default_insight_id", None)
    return s


def _probe_result(
    content: str | None = None,
    tool_calls: list | None = None,
    http_status: int | None = 200,
    error: str | None = None,
    latency_ms: float = 50.0,
    raw_response: dict | None = None,
):
    from backend.app.services.capability_probe.probe_llm_client import ProbeCallResult
    return ProbeCallResult(
        content=content,
        tool_calls=tool_calls,
        raw_response=raw_response or {},
        http_status=http_status,
        error=error,
        latency_ms=latency_ms,
    )


def _run_step(adapter, step_name: str, settings=None) -> CapabilityProbeStepResult:
    """Run a single probe step using the given adapter."""
    store = MagicMock()
    if settings is None:
        settings = MagicMock()
        settings.probe_context_smoke_size = 5
        settings.llm_adapter_type = "openai_chat"
    runner = ProbeRunner(store=store, settings=settings, adapter=adapter)
    step = CapabilityProbeStepResult(name=step_name)
    runner._dispatch(step)
    return step


# ---------------------------------------------------------------------------
# 1. Adapter factory
# ---------------------------------------------------------------------------


class TestAdapterFactory:
    def test_openai_chat_returns_openai_adapter(self):
        settings = _mock_settings(llm_adapter_type="openai_chat")
        with patch("backend.app.services.llm_adapters.get_settings", return_value=settings):
            # Clear cache so our patched settings take effect
            get_llm_adapter.cache_clear()
            adapter = get_llm_adapter()
        get_llm_adapter.cache_clear()
        assert isinstance(adapter, OpenAIChatAdapter)

    def test_unknown_adapter_type_raises_value_error(self):
        settings = _mock_settings(llm_adapter_type="unknown_type")
        with patch("backend.app.services.llm_adapters.get_settings", return_value=settings):
            get_llm_adapter.cache_clear()
            with pytest.raises(ValueError, match="Unknown llm_adapter_type"):
                get_llm_adapter()
        get_llm_adapter.cache_clear()

    def test_model_engine_adapter_instantiated_with_engine(self):
        """ModelEngineAskAdapter can be constructed directly with an injected engine."""
        engine = MagicMock()
        adapter = ModelEngineAskAdapter(engine, _mock_settings())
        assert adapter.get_name() == "model_engine_ask"


# ---------------------------------------------------------------------------
# 2. OpenAIChatAdapter
# ---------------------------------------------------------------------------


class TestOpenAIChatAdapter:
    @pytest.fixture
    def adapter(self):
        return OpenAIChatAdapter(_mock_settings())

    def _patch_send(self, adapter, result):
        adapter._client = MagicMock()
        adapter._client.send.return_value = result

    def test_ask_text_returns_content(self, adapter):
        self._patch_send(adapter, _probe_result(content="Hello world"))
        response = adapter.ask_text("Say hi")
        assert response.text == "Hello world"
        assert response.latency_ms == 50.0

    def test_ask_text_raises_on_400(self, adapter):
        self._patch_send(adapter, _probe_result(content=None, http_status=400, error="bad request"))
        with pytest.raises(AdapterInvocationError, match="400"):
            adapter.ask_text("Say hi")

    def test_ask_text_raises_on_network_error(self, adapter):
        self._patch_send(adapter, _probe_result(content=None, http_status=None, error="Connection refused"))
        with pytest.raises(AdapterInvocationError, match="Connection refused"):
            adapter.ask_text("Say hi")

    def test_ask_structured_clean_json_populates_parsed(self, adapter):
        content = '{"name": "x", "items": [{"id": 1, "label": "a"}]}'
        self._patch_send(adapter, _probe_result(content=content))
        response = adapter.ask_structured("Give JSON", {})
        assert response.text == content
        assert response.parsed_json == {"name": "x", "items": [{"id": 1, "label": "a"}]}

    def test_ask_structured_fence_wrapped_leaves_parsed_json_none(self, adapter):
        inner = '{"name": "x", "items": []}'
        content = f"```json\n{inner}\n```"
        self._patch_send(adapter, _probe_result(content=content))
        response = adapter.ask_structured("Give JSON", {})
        assert response.text == content
        # Fence-wrapped: adapter returns raw text; parsed_json is None
        # (the probe runner or caller is responsible for fence-stripping logic)
        assert response.parsed_json is None

    def test_ask_with_tools_populates_tool_calls(self, adapter):
        tc = [{"function": {"name": "get_weather", "arguments": '{"location": "London"}'}}]
        self._patch_send(adapter, _probe_result(tool_calls=tc))
        response = adapter.ask_with_tools("Weather?", tools=[{}])
        assert len(response.tool_calls) == 1
        assert response.tool_calls[0].name == "get_weather"
        assert response.tool_calls[0].arguments == {"location": "London"}

    def test_ask_with_tools_raises_on_400(self, adapter):
        self._patch_send(adapter, _probe_result(http_status=400, error="tools not supported"))
        with pytest.raises(AdapterInvocationError):
            adapter.ask_with_tools("Weather?", tools=[{}])

    def test_feature_support(self, adapter):
        fs = adapter.get_feature_support()
        assert fs.structured_output is True
        assert fs.tool_calling is True
        assert fs.context_input is True

    def test_get_name(self, adapter):
        assert adapter.get_name() == "openai_chat"

    def test_context_prepended_to_prompt(self, adapter):
        """Context is prepended to the user prompt in the messages list."""
        self._patch_send(adapter, _probe_result(content="OK"))
        adapter.ask_text("My question", context="Some context")
        call_args = adapter._client.send.call_args
        messages = call_args[0][0]
        user_msg = messages[-1]["content"]
        assert "Some context" in user_msg
        assert "My question" in user_msg

    def test_system_prompt_added_as_system_message(self, adapter):
        self._patch_send(adapter, _probe_result(content="OK"))
        adapter.ask_text("Question", system_prompt="Be concise.")
        call_args = adapter._client.send.call_args
        messages = call_args[0][0]
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "Be concise."


# ---------------------------------------------------------------------------
# 3. ModelEngineAskAdapter
# ---------------------------------------------------------------------------


class TestModelEngineAskAdapter:
    @pytest.fixture
    def engine(self):
        return MagicMock()

    @pytest.fixture
    def adapter(self, engine):
        return ModelEngineAskAdapter(engine, _mock_settings())

    def test_ask_text_string_response(self, adapter, engine):
        engine.ask.return_value = "Here is the answer."
        response = adapter.ask_text("What is this?")
        assert response.text == "Here is the answer."
        assert response.parsed_json is None

    def test_ask_text_json_string_response(self, adapter, engine):
        engine.ask.return_value = '{"result": "ok"}'
        response = adapter.ask_text("JSON please")
        assert response.parsed_json == {"result": "ok"}

    def test_ask_text_dict_response(self, adapter, engine):
        engine.ask.return_value = {"answer": 42}
        response = adapter.ask_text("Dict response")
        assert response.parsed_json == {"answer": 42}

    def test_ask_text_engine_error_raises(self, adapter, engine):
        engine.ask.side_effect = RuntimeError("engine down")
        with pytest.raises(AdapterInvocationError, match="engine down"):
            adapter.ask_text("anything")

    def test_ask_structured_passes_schema_in_param_dict(self, adapter, engine):
        engine.ask.return_value = '{"name": "x", "items": []}'
        schema = {"type": "object"}
        adapter.ask_structured("Give me JSON", schema)
        call_kwargs = engine.ask.call_args[1]
        assert "param_dict" in call_kwargs
        assert call_kwargs["param_dict"]["schema"] == schema

    def test_ask_structured_json_string_populates_parsed_json(self, adapter, engine):
        payload = {"name": "project", "items": [{"id": 1, "label": "a"}]}
        engine.ask.return_value = json.dumps(payload)
        response = adapter.ask_structured("Give JSON", {})
        assert response.parsed_json == payload

    def test_ask_structured_temperature_in_param_dict(self, adapter, engine):
        engine.ask.return_value = "{}"
        adapter.ask_structured("x", {}, temperature=0.1)
        call_kwargs = engine.ask.call_args[1]
        assert call_kwargs["param_dict"]["temperature"] == 0.1

    def test_ask_with_tools_passes_tools_in_param_dict(self, adapter, engine):
        engine.ask.return_value = "The weather is sunny."
        tools = [{"type": "function", "function": {"name": "get_weather"}}]
        adapter.ask_with_tools("Weather?", tools=tools)
        call_kwargs = engine.ask.call_args[1]
        assert call_kwargs["param_dict"]["tools"] == tools
        assert call_kwargs["param_dict"]["tool_choice"] == "auto"

    def test_ask_with_tools_dict_response_extracts_tool_calls(self, adapter, engine):
        engine.ask.return_value = {
            "tool_calls": [
                {"function": {"name": "get_weather", "arguments": '{"location": "Paris"}'}}
            ]
        }
        tools = [{"type": "function"}]
        response = adapter.ask_with_tools("Weather?", tools=tools)
        assert len(response.tool_calls) == 1
        assert response.tool_calls[0].name == "get_weather"

    def test_ask_with_tools_text_response_no_tool_calls(self, adapter, engine):
        engine.ask.return_value = "I don't know the weather."
        response = adapter.ask_with_tools("Weather?", tools=[{}])
        assert len(response.tool_calls) == 0
        assert response.text == "I don't know the weather."

    def test_ask_with_tools_engine_error_raises(self, adapter, engine):
        engine.ask.side_effect = ValueError("tools rejected")
        with pytest.raises(AdapterInvocationError, match="tools rejected"):
            adapter.ask_with_tools("x", tools=[{}])

    def test_feature_support(self, adapter):
        fs = adapter.get_feature_support()
        assert fs.structured_output is True
        assert fs.tool_calling is True
        assert fs.context_input is True

    def test_get_name(self, adapter):
        assert adapter.get_name() == "model_engine_ask"

    def test_context_passed_to_engine(self, adapter, engine):
        engine.ask.return_value = "OK"
        adapter.ask_text("question", context="my context")
        call_kwargs = engine.ask.call_args[1]
        assert call_kwargs["context"] == "my context"

    def test_room_id_from_settings(self, adapter, engine):
        engine.ask.return_value = "OK"
        adapter._settings.model_engine_default_room_id = "room-xyz"
        adapter.ask_text("question")
        call_kwargs = engine.ask.call_args[1]
        assert call_kwargs["room_id"] == "room-xyz"


# ---------------------------------------------------------------------------
# 4. Probe step integration with adapter
# ---------------------------------------------------------------------------


def _mock_adapter(
    text: str = "",
    parsed_json=None,
    tool_calls: list[ToolCallResult] | None = None,
    latency_ms: float = 50.0,
    raise_invocation: str | None = None,
    raise_unsupported: str | None = None,
    feature_structured: bool = True,
    feature_tools: bool = True,
):
    """Build a mock LLMAdapter with controlled responses."""
    adapter = MagicMock()
    adapter.get_name.return_value = "mock_adapter"
    adapter.get_model_name.return_value = "mock-model"
    adapter.get_feature_support.return_value = AdapterFeatureSupport(
        structured_output=feature_structured,
        tool_calling=feature_tools,
        context_input=True,
    )

    response = AdapterResponse(
        text=text,
        parsed_json=parsed_json,
        tool_calls=tool_calls or [],
        latency_ms=latency_ms,
    )

    if raise_invocation:
        exc = AdapterInvocationError(raise_invocation)
        adapter.ask_text.side_effect = exc
        adapter.ask_structured.side_effect = exc
        adapter.ask_with_tools.side_effect = exc
    elif raise_unsupported:
        exc = AdapterUnsupportedFeatureError(raise_unsupported)
        adapter.ask_with_tools.side_effect = exc
        adapter.ask_structured.side_effect = exc
        adapter.ask_text.return_value = response
    else:
        adapter.ask_text.return_value = response
        adapter.ask_structured.return_value = response
        adapter.ask_with_tools.return_value = response

    return adapter


class TestProbeStructuredJsonStep:
    def test_pass_on_clean_json_with_valid_schema(self):
        payload = {"name": "project", "items": [{"id": 1, "label": "a"}, {"id": 2, "label": "b"}]}
        adapter = _mock_adapter(
            text=json.dumps(payload),
            parsed_json=payload,
        )
        step = _run_step(adapter, "structured_json_output")
        assert step.assessment == CapabilityAssessment.pass_

    def test_warning_when_feature_flag_true_but_raises_unsupported(self):
        adapter = _mock_adapter(raise_unsupported="json mode unsupported")
        step = _run_step(adapter, "structured_json_output")
        assert step.assessment == CapabilityAssessment.unknown
        assert step.status == StepStatus.skipped

    def test_unknown_when_feature_flag_false(self):
        adapter = _mock_adapter(feature_structured=False)
        step = _run_step(adapter, "structured_json_output")
        assert step.assessment == CapabilityAssessment.unknown
        assert step.status == StepStatus.skipped

    def test_fail_on_invocation_error(self):
        adapter = _mock_adapter(raise_invocation="HTTP 500")
        step = _run_step(adapter, "structured_json_output")
        assert step.assessment == CapabilityAssessment.fail
        assert step.status == StepStatus.failed

    def test_fail_on_empty_response(self):
        adapter = _mock_adapter(text="", parsed_json=None)
        step = _run_step(adapter, "structured_json_output")
        assert step.assessment == CapabilityAssessment.fail

    def test_warning_on_fence_wrapped_valid_json(self):
        """Adapter returns raw text (fence-wrapped); parsed_json is None but fence-strip succeeds."""
        inner = '{"name": "project", "items": [{"id": 1, "label": "a"}]}'
        content = f"```json\n{inner}\n```"
        adapter = _mock_adapter(text=content, parsed_json=None)
        step = _run_step(adapter, "structured_json_output")
        assert step.assessment == CapabilityAssessment.warning


class TestProbeToolCallStep:
    def test_pass_on_correct_tool_call(self):
        tool_calls = [ToolCallResult(name="get_weather", arguments={"location": "London"})]
        adapter = _mock_adapter(tool_calls=tool_calls)
        step = _run_step(adapter, "tool_call_readiness")
        assert step.assessment == CapabilityAssessment.pass_
        assert step.status == StepStatus.passed

    def test_warning_on_wrong_tool_name(self):
        tool_calls = [ToolCallResult(name="search_web", arguments={})]
        adapter = _mock_adapter(tool_calls=tool_calls)
        step = _run_step(adapter, "tool_call_readiness")
        assert step.assessment == CapabilityAssessment.warning

    def test_warning_when_text_reply_no_tools(self):
        adapter = _mock_adapter(text="The weather is sunny.", tool_calls=[])
        step = _run_step(adapter, "tool_call_readiness")
        assert step.assessment == CapabilityAssessment.warning

    def test_unknown_when_feature_flag_false(self):
        adapter = _mock_adapter(feature_tools=False)
        step = _run_step(adapter, "tool_call_readiness")
        assert step.assessment == CapabilityAssessment.unknown
        assert step.status == StepStatus.skipped

    def test_unknown_on_unsupported_feature_error(self):
        adapter = _mock_adapter(raise_unsupported="tools not supported")
        step = _run_step(adapter, "tool_call_readiness")
        assert step.assessment == CapabilityAssessment.unknown
        assert step.status == StepStatus.skipped

    def test_fail_on_invocation_error(self):
        adapter = _mock_adapter(raise_invocation="Connection refused")
        step = _run_step(adapter, "tool_call_readiness")
        assert step.assessment == CapabilityAssessment.fail

    def test_fail_on_no_content_no_tool_calls(self):
        adapter = _mock_adapter(text="", tool_calls=[])
        step = _run_step(adapter, "tool_call_readiness")
        assert step.assessment == CapabilityAssessment.fail


class TestProbeRunMetadata:
    def test_run_metadata_includes_adapter_info(self):
        """ProbeRunner populates probe_meta with adapter_name and model_name."""
        adapter = _mock_adapter(text="OK")
        store = MagicMock()

        settings = MagicMock()
        settings.probe_context_smoke_size = 2
        settings.llm_adapter_type = "mock_adapter"

        run = MagicMock()
        run.steps = []
        run.completed_steps = 0
        run.probe_meta = {}
        store.get_run.return_value = run
        store.get_steps.return_value = []

        runner = ProbeRunner(store=store, settings=settings, adapter=adapter)
        # Trigger only the metadata-setting portion by checking what run.probe_meta would be
        # after run() sets it. We mock store.save_report to prevent further processing.
        store.save_report.return_value = None

        runner.run("test-run-id")

        assert run.probe_meta.get("adapter_name") == "mock_adapter"
        assert run.probe_meta.get("model_name") == "mock-model"
