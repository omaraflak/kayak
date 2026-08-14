"""Tests for which model writes a conversation's title.

A generated title is a prompt like any other, so it must go to the model the
conversation itself runs on. Reaching for a global default meant a conversation on a
locally served model could quietly bill a cloud provider -- or fail, because the
provider it picked was never configured.
"""

import pytest

from backend.app.models import AgentConfig
from backend.app.routes import conversations as conversations_module
from backend.app.routes.conversations import resolve_conversation_model


def _agent(agent_id: str, model: str) -> AgentConfig:
    return AgentConfig(
        id=agent_id,
        name=agent_id,
        description="",
        model=model,
        allowed_tools=[],
        tool_permissions={},
    )


@pytest.fixture
def agents(monkeypatch):
    """Installs a registry of known agents for the resolver to read."""
    registry = {
        "coding": _agent("coding", "anthropic/claude-3-5-sonnet-20241022"),
        "general": _agent("general", "gemini/gemini-2.5-flash"),
        "local": _agent("local", "vllm/Qwen/Qwen2.5-Coder-7B-Instruct"),
    }
    monkeypatch.setattr(
        conversations_module.agent_manager, "get_agent", registry.get
    )
    return registry


class TestResolveConversationModel:
    def test_uses_the_model_the_conversation_will_run_on(self, agents):
        assert resolve_conversation_model("coding") == "anthropic/claude-3-5-sonnet-20241022"

    def test_a_locally_served_agent_titles_on_its_own_local_model(self, agents):
        # Previously this fell through to a cloud default, so an entirely local setup
        # made a paid API call just to name the conversation.
        assert resolve_conversation_model("local") == "vllm/Qwen/Qwen2.5-Coder-7B-Instruct"

    def test_an_unknown_agent_falls_back_the_way_the_engine_does(self, agents):
        # The engine resolves an unknown agent to "general", so the title comes from
        # the same model that will answer.
        assert resolve_conversation_model("does-not-exist") == "gemini/gemini-2.5-flash"

    def test_no_model_is_invented_when_nothing_resolves(self, monkeypatch):
        monkeypatch.setattr(
            conversations_module.agent_manager, "get_agent", lambda agent_id: None
        )

        # There is no global default to reach for; the caller skips titling instead.
        assert resolve_conversation_model("anything") is None
