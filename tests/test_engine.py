"""End-to-end tests for the agent turn loop.

The scenarios here are the ones that used to leave a conversation permanently
unusable: a turn cancelled between requesting a tool call and recording its result,
and a turn that exhausts its iteration ceiling with calls still outstanding. In both
cases the next turn must still be able to build a message list a provider accepts.
"""

import asyncio
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List
import pytest
from backend.app.agent import engine as engine_module
from backend.app.agent.engine import agent_engine
from backend.app.agent.history import repair_tool_call_pairing
from backend.app.config import settings
from backend.app.database import (
    add_message,
    create_conversation,
    get_conversation,
    get_messages,
    init_db,
)
from backend.app.models import (
    AgentConfig,
    ConversationStatus,
    MessageRole,
    ToolPermission,
)


def _tool_call_turn(name: str = "run_command", call_id: str = "call_1") -> List[Dict[str, Any]]:
    """A streamed turn that asks for one tool call."""
    return [
        {"type": "tool_call_delta", "index": 0, "id": call_id, "name": name, "arguments": "{}"}
    ]


def _text_turn(text: str = "all done") -> List[Dict[str, Any]]:
    """A streamed turn that answers in prose and stops."""
    return [{"type": "token", "content": text}]


@pytest.fixture
async def agent_env(tmp_path: Path, monkeypatch):
    """Isolates database, workspace, and agent profile for an engine run."""
    monkeypatch.setattr(settings, "DB_PATH", tmp_path / "engine.db")
    monkeypatch.setattr(settings, "WORKSPACES_DIR", tmp_path / "workspaces")
    await init_db()

    agent = AgentConfig(
        id="tester",
        name="Tester",
        description="test",
        model="test/model",
        allowed_tools=["run_command"],
        tool_permissions={},
    )
    monkeypatch.setattr(
        engine_module.agent_manager, "get_agent", lambda agent_id: agent
    )
    monkeypatch.setattr(
        engine_module.tool_registry, "get_tool_definitions", lambda allowed_names=None: []
    )
    return agent


def _stub_stream(monkeypatch, turns: List[List[Dict[str, Any]]]):
    """Replays a scripted sequence of model turns, one per engine iteration."""
    remaining = list(turns)

    async def fake_stream(**kwargs) -> AsyncGenerator[Dict[str, Any], None]:
        chunks = remaining.pop(0) if remaining else _text_turn("fallback")
        for chunk in chunks:
            yield chunk

    monkeypatch.setattr(engine_module, "generate_completion_stream", fake_stream)


def _stub_tool(monkeypatch, handler):
    """Replaces tool execution with a test double."""

    async def fake_execute(name: str, arguments: Dict[str, Any], context: Dict[str, Any]) -> str:
        return await handler(name, arguments, context)

    monkeypatch.setattr(engine_module.tool_registry, "execute_tool", fake_execute)


async def _drain(conversation_id: str, **kwargs) -> List[Dict[str, Any]]:
    return [event async for event in agent_engine.run(conversation_id, **kwargs)]


def _assert_history_is_well_formed(messages) -> None:
    """Every tool call must have exactly one result, and no result may be an orphan."""
    requested = [
        call["id"]
        for m in messages
        if m.role == MessageRole.ASSISTANT and m.tool_calls
        for call in m.tool_calls
    ]
    answered = [m.tool_call_id for m in messages if m.role == MessageRole.TOOL]

    assert sorted(requested) == sorted(answered), (
        f"unpaired tool calls: requested={requested} answered={answered}"
    )


@pytest.mark.asyncio
async def test_completed_turn_pairs_every_tool_call(agent_env, monkeypatch):
    _stub_stream(monkeypatch, [_tool_call_turn(), _text_turn()])
    _stub_tool(monkeypatch, lambda name, args, ctx: _ok())

    conversation = await create_conversation(title="t", agent_id="tester")
    await add_message(conversation.id, MessageRole.USER, content="go")

    events = await _drain(conversation.id)

    assert events[-1]["type"] == "done"
    _assert_history_is_well_formed(await get_messages(conversation.id))


async def _ok() -> str:
    return "tool output"


@pytest.mark.asyncio
async def test_cancelling_mid_tool_call_leaves_usable_history(agent_env, monkeypatch):
    """Cancelling between a tool call and its result must not poison the conversation."""
    tool_entered = asyncio.Event()

    async def hanging_tool(name, args, ctx):
        tool_entered.set()
        await asyncio.sleep(60)
        return "never reached"

    _stub_stream(monkeypatch, [_tool_call_turn(), _text_turn()])
    _stub_tool(monkeypatch, hanging_tool)

    conversation = await create_conversation(title="t", agent_id="tester")
    await add_message(conversation.id, MessageRole.USER, content="go")

    task = asyncio.create_task(_drain(conversation.id))
    await asyncio.wait_for(tool_entered.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    messages = await get_messages(conversation.id)
    _assert_history_is_well_formed(messages)

    # And the conversation must not be left pinned in RUNNING.
    reloaded = await get_conversation(conversation.id)
    assert reloaded.status == ConversationStatus.ACTIVE


@pytest.mark.asyncio
async def test_history_repair_rescues_an_already_broken_conversation(agent_env):
    """Conversations corrupted before this fix must become usable again on read."""
    conversation = await create_conversation(title="t", agent_id="tester")
    await add_message(conversation.id, MessageRole.USER, content="go")
    await add_message(
        conversation.id,
        MessageRole.ASSISTANT,
        tool_calls=[
            {
                "id": "orphan",
                "type": "function",
                "function": {"name": "run_command", "arguments": "{}"},
            }
        ],
    )

    repaired = repair_tool_call_pairing(await get_messages(conversation.id))

    _assert_history_is_well_formed(repaired)


@pytest.mark.asyncio
async def test_iteration_ceiling_reports_itself_and_closes_calls(agent_env, monkeypatch):
    _stub_stream(monkeypatch, [_tool_call_turn(call_id=f"c{i}") for i in range(5)])
    _stub_tool(monkeypatch, lambda name, args, ctx: _ok())

    conversation = await create_conversation(title="t", agent_id="tester")
    await add_message(conversation.id, MessageRole.USER, content="go")

    events = await _drain(conversation.id, max_iterations=3)

    # The user is told the agent stopped early rather than being shown a turn that
    # simply ends without an answer.
    assert any(e["type"] == "max_iterations" for e in events)
    _assert_history_is_well_formed(await get_messages(conversation.id))


@pytest.mark.asyncio
async def test_tool_absent_from_allowlist_is_refused(agent_env, monkeypatch):
    """A model naming a tool it was never offered must not reach the executor."""
    executed: List[str] = []

    async def recording_tool(name, args, ctx):
        executed.append(name)
        return "should not run"

    _stub_stream(
        monkeypatch,
        [_tool_call_turn(name="delete_everything", call_id="x1"), _text_turn()],
    )
    _stub_tool(monkeypatch, recording_tool)

    conversation = await create_conversation(title="t", agent_id="tester")
    await add_message(conversation.id, MessageRole.USER, content="go")

    events = await _drain(conversation.id)

    assert executed == []
    denial = next(e for e in events if e["type"] == "tool_call_result")
    assert denial["is_error"] is True
    assert "not permitted" in denial["output"]
    _assert_history_is_well_formed(await get_messages(conversation.id))


@pytest.mark.asyncio
async def test_ask_user_tool_waits_for_approval(agent_env, monkeypatch):
    executed: List[str] = []

    async def recording_tool(name, args, ctx):
        executed.append(name)
        return "ran"

    agent_env.tool_permissions = {"run_command": ToolPermission.ASK_USER}
    _stub_stream(monkeypatch, [_tool_call_turn(call_id="gate1"), _text_turn()])
    _stub_tool(monkeypatch, recording_tool)

    conversation = await create_conversation(title="t", agent_id="tester")
    await add_message(conversation.id, MessageRole.USER, content="go")

    events: List[Dict[str, Any]] = []

    async def consume():
        async for event in agent_engine.run(conversation.id):
            events.append(event)
            if event["type"] == "tool_approval_required":
                # Approve out of band, exactly as the REST endpoint does.
                engine_module.approval_registry.resolve(event["id"], True)

    await asyncio.wait_for(consume(), timeout=10)

    assert any(e["type"] == "tool_approval_required" for e in events)
    assert executed == ["run_command"]
    _assert_history_is_well_formed(await get_messages(conversation.id))


@pytest.mark.asyncio
async def test_rejected_tool_call_is_not_executed(agent_env, monkeypatch):
    executed: List[str] = []

    async def recording_tool(name, args, ctx):
        executed.append(name)
        return "ran"

    agent_env.tool_permissions = {"run_command": ToolPermission.ASK_USER}
    _stub_stream(monkeypatch, [_tool_call_turn(call_id="gate2"), _text_turn()])
    _stub_tool(monkeypatch, recording_tool)

    conversation = await create_conversation(title="t", agent_id="tester")
    await add_message(conversation.id, MessageRole.USER, content="go")

    async def consume():
        async for event in agent_engine.run(conversation.id):
            if event["type"] == "tool_approval_required":
                engine_module.approval_registry.resolve(event["id"], False)

    await asyncio.wait_for(consume(), timeout=10)

    assert executed == []
    _assert_history_is_well_formed(await get_messages(conversation.id))


@pytest.mark.asyncio
async def test_tool_call_ids_are_unique_across_turns(agent_env, monkeypatch):
    """Providers that omit ids must not produce colliding ids on successive turns."""
    anonymous_turn = [
        {"type": "tool_call_delta", "index": 0, "name": "run_command", "arguments": "{}"}
    ]
    _stub_stream(monkeypatch, [anonymous_turn, anonymous_turn, _text_turn()])
    _stub_tool(monkeypatch, lambda name, args, ctx: _ok())

    conversation = await create_conversation(title="t", agent_id="tester")
    await add_message(conversation.id, MessageRole.USER, content="go")

    await _drain(conversation.id)

    messages = await get_messages(conversation.id)
    call_ids = [
        call["id"]
        for m in messages
        if m.role == MessageRole.ASSISTANT and m.tool_calls
        for call in m.tool_calls
    ]
    assert len(call_ids) == len(set(call_ids)) == 2
    _assert_history_is_well_formed(messages)


@pytest.mark.asyncio
async def test_stream_error_does_not_leave_dangling_calls(agent_env, monkeypatch):
    _stub_stream(
        monkeypatch,
        [
            [
                {"type": "token", "content": "partial"},
                {"type": "error", "error": "provider exploded"},
            ]
        ],
    )
    _stub_tool(monkeypatch, lambda name, args, ctx: _ok())

    conversation = await create_conversation(title="t", agent_id="tester")
    await add_message(conversation.id, MessageRole.USER, content="go")

    events = await _drain(conversation.id)

    assert any(e["type"] == "error" for e in events)
    _assert_history_is_well_formed(await get_messages(conversation.id))
    reloaded = await get_conversation(conversation.id)
    assert reloaded.status == ConversationStatus.ACTIVE


@pytest.mark.asyncio
async def test_read_only_tools_execute_concurrently(agent_env, monkeypatch):
    """Multiple read-only tool calls in a turn must run concurrently via asyncio.gather."""
    agent_env.allowed_tools = ["read_file", "list_directory"]

    running_tools = 0
    max_concurrent_tools = 0

    async def concurrent_mock_tool(
        name: str, args: Dict[str, Any], context: Dict[str, Any]
    ) -> str:
        nonlocal running_tools, max_concurrent_tools
        running_tools += 1
        max_concurrent_tools = max(max_concurrent_tools, running_tools)
        await asyncio.sleep(0.05)
        running_tools -= 1
        return f"{name} output"

    _stub_stream(
        monkeypatch,
        [
            [
                {
                    "type": "tool_call_delta",
                    "index": 0,
                    "id": "call_read1",
                    "name": "read_file",
                    "arguments": "{}",
                },
                {
                    "type": "tool_call_delta",
                    "index": 1,
                    "id": "call_read2",
                    "name": "list_directory",
                    "arguments": "{}",
                },
            ],
            _text_turn("done"),
        ],
    )
    _stub_tool(monkeypatch, concurrent_mock_tool)

    conversation = await create_conversation(title="t", agent_id="tester")
    await add_message(conversation.id, MessageRole.USER, content="read files")

    events = await _drain(conversation.id)

    assert max_concurrent_tools == 2
    assert any(
        e["type"] == "tool_call_result" and e["id"] == "call_read1"
        for e in events
    )
    assert any(
        e["type"] == "tool_call_result" and e["id"] == "call_read2"
        for e in events
    )
    _assert_history_is_well_formed(await get_messages(conversation.id))


@pytest.mark.asyncio
async def test_mixed_or_mutating_tools_execute_sequentially(
    agent_env, monkeypatch
):
    """Turns with any mutating tool must execute serially to prevent race conditions."""
    agent_env.allowed_tools = ["run_command", "read_file"]

    running_tools = 0
    max_concurrent_tools = 0

    async def serial_mock_tool(
        name: str, args: Dict[str, Any], context: Dict[str, Any]
    ) -> str:
        nonlocal running_tools, max_concurrent_tools
        running_tools += 1
        max_concurrent_tools = max(max_concurrent_tools, running_tools)
        await asyncio.sleep(0.05)
        running_tools -= 1
        return f"{name} output"

    _stub_stream(
        monkeypatch,
        [
            [
                {
                    "type": "tool_call_delta",
                    "index": 0,
                    "id": "call_run",
                    "name": "run_command",
                    "arguments": "{}",
                },
                {
                    "type": "tool_call_delta",
                    "index": 1,
                    "id": "call_read",
                    "name": "read_file",
                    "arguments": "{}",
                },
            ],
            _text_turn("done"),
        ],
    )
    _stub_tool(monkeypatch, serial_mock_tool)

    conversation = await create_conversation(title="t", agent_id="tester")
    await add_message(conversation.id, MessageRole.USER, content="run and read")

    events = await _drain(conversation.id)

    assert max_concurrent_tools == 1
    assert any(
        e["type"] == "tool_call_result" and e["id"] == "call_run" for e in events
    )
    assert any(
        e["type"] == "tool_call_result" and e["id"] == "call_read" for e in events
    )
    _assert_history_is_well_formed(await get_messages(conversation.id))
