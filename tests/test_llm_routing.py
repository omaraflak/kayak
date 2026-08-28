"""Tests for provider routing and the tool-support fallback.

Both behaviours previously failed quietly: a locally served model could be sent to a
cloud provider, and any error mentioning "400" downgraded the agent to a tool-less
chatbot without telling anyone.
"""

import pytest
from backend.app.llm import _is_tool_unsupported_error, resolve_provider


class TestResolveProvider:
    @pytest.mark.parametrize(
        "model,expected",
        [
            ("gemini/gemini-3.6-flash", "gemini"),
            ("openai/gpt-4o", "openai"),
            ("anthropic/claude-sonnet-4", "anthropic"),
            ("huggingface/meta-llama/Llama-3-8B", "huggingface"),
            ("hf/mistralai/Mistral-7B", "hf"),
            ("vllm/Qwen/Qwen2.5-7B-Instruct", "vllm"),
            ("openai/vllm/Qwen/Qwen2.5-7B", "vllm"),
        ],
    )
    def test_explicit_prefix_wins(self, model: str, expected: str):
        assert resolve_provider(model) == expected

    def test_locally_served_model_named_after_another_provider(self):
        # The old substring check saw "gpt" and routed this to OpenAI, so it never
        # received the local api_base and could not run at all.
        assert resolve_provider("vllm/openai/gpt-oss-20b") == "vllm"

    @pytest.mark.parametrize(
        "model,expected",
        [
            ("gpt-4o", "openai"),
            ("o1-preview", "openai"),
            ("claude-sonnet-4", "anthropic"),
            ("gemini-3.6-flash", "gemini"),
        ],
    )
    def test_bare_model_names_fall_back_to_prefix_heuristics(self, model, expected):
        assert resolve_provider(model) == expected

    def test_unknown_provider_returns_none(self):
        assert resolve_provider("ollama/llama3") is None
        assert resolve_provider("some-local-model") is None


class TestIsToolUnsupportedError:
    @pytest.mark.parametrize(
        "message",
        [
            "This model does not support tool use",
            "Function calling is not supported for this model",
            "Unsupported parameter: 'tools' is not supported with this model",
            "No endpoints found that support tool use",
        ],
    )
    def test_genuine_tool_rejections_are_detected(self, message: str):
        assert _is_tool_unsupported_error(Exception(message))

    @pytest.mark.parametrize(
        "message",
        [
            "Error code: 400 - invalid_request_error: messages are malformed",
            "Error code: 400 - context_length_exceeded",
            "AuthenticationError: Incorrect API key provided",
            "RateLimitError: 429 Too Many Requests",
            "Error code: 400 - tool_call_id not found in previous messages",
        ],
    )
    def test_unrelated_failures_are_not_swallowed(self, message: str):
        # Retrying these without tools hides the real fault and silently strips the
        # agent's capabilities.
        assert not _is_tool_unsupported_error(Exception(message))


class TestVllmApiBaseFollowsTheServer:
    """The container may be published on a neighbouring port when the default is
    taken (on launcher installs Kayak itself occupies it), so chat requests must
    go where the server actually answers."""

    def test_a_ready_server_on_a_fallback_port_is_used(self, monkeypatch):
        from backend.app import llm
        from backend.app.vllm import metal
        from backend.app.vllm.manager import vllm_manager
        from backend.app.vllm.models import MetalStatus, VLLMDeploymentProgress, VLLMServerState

        monkeypatch.setattr(metal, "read_status", lambda: MetalStatus())
        monkeypatch.setattr(
            vllm_manager,
            "get_status",
            lambda: VLLMDeploymentProgress(
                model_id="Org/Model",
                state=VLLMServerState.READY,
                port=8002,
                endpoint="http://localhost:8002/v1",
            ),
        )
        monkeypatch.delenv("VLLM_API_BASE", raising=False)

        kwargs = llm._build_llm_kwargs("vllm/Org/Model", messages=[])

        assert kwargs["api_base"] == "http://localhost:8002/v1"

    def test_without_a_ready_server_the_configured_base_stands(self, monkeypatch):
        from backend.app import llm
        from backend.app.config import settings
        from backend.app.vllm import metal
        from backend.app.vllm.manager import vllm_manager
        from backend.app.vllm.models import MetalStatus, VLLMDeploymentProgress

        monkeypatch.setattr(metal, "read_status", lambda: MetalStatus())
        monkeypatch.setattr(vllm_manager, "get_status", lambda: VLLMDeploymentProgress())
        monkeypatch.delenv("VLLM_API_BASE", raising=False)

        kwargs = llm._build_llm_kwargs("vllm/Org/Model", messages=[])

        assert kwargs["api_base"] == settings.VLLM_API_BASE
