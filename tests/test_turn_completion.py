"""Tests that a turn always tells the client it is over.

The composer stays in its generating state until the stream says the turn ended. A
turn that failed used to return without that event, leaving the client to infer the
end from the error alone -- and anything that did not special-case errors sat waiting
for an event that was never coming.
"""

from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List

import pytest

from backend.app.agent import engine as engine_module
from backend.app.agent.engine import agent_engine
from backend.app.config import settings
from backend.app.database import add_message, create_conversation, get_messages, init_db
from backend.app.models import AgentConfig, MessageRole


@pytest.fixture
async def agent_env(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "DB_PATH", tmp_path / "turns.db")
    monkeypatch.setattr(settings, "WORKSPACES_DIR", tmp_path / "workspaces")
    await init_db()

    agent = AgentConfig(
        id="tester",
        name="Tester",
        description="test",
        model="test/model",
        allowed_tools=[],
        tool_permissions={},
    )
    monkeypatch.setattr(engine_module.agent_manager, "get_agent", lambda agent_id: agent)
    monkeypatch.setattr(
        engine_module.tool_registry, "get_tool_definitions", lambda allowed_names=None: []
    )
    return agent


def _stub_stream(monkeypatch, chunks: List[Dict[str, Any]]) -> None:
    async def fake_stream(**kwargs: Any) -> AsyncGenerator[Dict[str, Any], None]:
        for chunk in chunks:
            yield chunk

    monkeypatch.setattr(engine_module, "generate_completion_stream", fake_stream)


async def _run(conversation_id: str) -> List[Dict[str, Any]]:
    return [event async for event in agent_engine.run(conversation_id)]


class TestDoneIsAlwaysSent:
    async def test_after_a_stream_error(self, agent_env, monkeypatch):
        _stub_stream(
            monkeypatch,
            [
                {"type": "token", "content": "partial answer"},
                {"type": "error", "error": "provider exploded"},
            ],
        )
        conversation = await create_conversation(title="t", agent_id="tester")
        await add_message(conversation.id, MessageRole.USER, content="go")

        events = await _run(conversation.id)

        types = [event["type"] for event in events]
        assert "error" in types
        assert types[-1] == "done"

    async def test_the_partial_answer_is_kept(self, agent_env, monkeypatch):
        # Whatever streamed before the failure is real work; losing it would make the
        # transcript disagree with what the user watched arrive.
        _stub_stream(
            monkeypatch,
            [
                {"type": "token", "content": "half an answer"},
                {"type": "error", "error": "provider exploded"},
            ],
        )
        conversation = await create_conversation(title="t", agent_id="tester")
        await add_message(conversation.id, MessageRole.USER, content="go")

        await _run(conversation.id)

        assistant = [
            message
            for message in await get_messages(conversation.id)
            if message.role == MessageRole.ASSISTANT
        ]
        assert [message.content for message in assistant] == ["half an answer"]

    async def test_a_failure_before_the_first_token_still_ends(self, agent_env, monkeypatch):
        _stub_stream(monkeypatch, [{"type": "error", "error": "no credentials"}])
        conversation = await create_conversation(title="t", agent_id="tester")
        await add_message(conversation.id, MessageRole.USER, content="go")

        events = await _run(conversation.id)

        assert [event["type"] for event in events] == ["error", "done"]

    async def test_nothing_empty_is_written_for_a_failure_with_no_output(
        self, agent_env, monkeypatch
    ):
        _stub_stream(monkeypatch, [{"type": "error", "error": "no credentials"}])
        conversation = await create_conversation(title="t", agent_id="tester")
        await add_message(conversation.id, MessageRole.USER, content="go")

        await _run(conversation.id)

        roles = [message.role for message in await get_messages(conversation.id)]
        assert roles == [MessageRole.USER]

    async def test_an_unresolvable_conversation_still_ends(self, agent_env):
        events = await _run("does-not-exist")

        assert [event["type"] for event in events] == ["error", "done"]
