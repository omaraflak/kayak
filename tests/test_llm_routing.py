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
