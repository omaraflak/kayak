"""Tests for reverting, retrying, and branching a conversation.

All three rewrite history, so the invariant that matters throughout is the one the
providers enforce: every assistant tool call must be followed by its result. A cut in
the wrong place produces a conversation that cannot be sent to any model.
"""

from pathlib import Path

import pytest

from backend.app.config import settings
from backend.app.database import (
    add_message,
    copy_messages_through,
    create_conversation,
    delete_messages_from,
    get_messages,
    get_preceding_user_message,
    init_db,
)
from backend.app.models import MessageRole


@pytest.fixture
async def database(tmp_path: Path, monkeypatch):
    """Points the data layer at a fresh database for each test."""
    monkeypatch.setattr(settings, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(settings, "WORKSPACES_DIR", tmp_path / "workspaces")
    await init_db()
    yield


async def _seed_two_turns(conversation_id: str) -> dict:
    """Builds a history of two agent turns, the first of which used a tool."""
    first_prompt = await add_message(conversation_id, MessageRole.USER, content="list the files")
    first_reply = await add_message(
        conversation_id,
        MessageRole.ASSISTANT,
        content="Let me look.",
        tool_calls=[{"id": "call_1", "type": "function", "function": {"name": "list_files", "arguments": "{}"}}],
    )
    tool_result = await add_message(
        conversation_id, MessageRole.TOOL, content="a.py\nb.py", tool_call_id="call_1", name="list_files"
    )
    first_summary = await add_message(
        conversation_id, MessageRole.ASSISTANT, content="There are two files."
    )
    second_prompt = await add_message(conversation_id, MessageRole.USER, content="what is in a.py?")
    second_reply = await add_message(
        conversation_id, MessageRole.ASSISTANT, content="It defines main()."
    )

    return {
        "first_prompt": first_prompt,
        "first_reply": first_reply,
        "tool_result": tool_result,
        "first_summary": first_summary,
        "second_prompt": second_prompt,
        "second_reply": second_reply,
    }


@pytest.mark.asyncio
class TestDeleteMessagesFrom:
    async def test_removes_the_anchor_and_everything_after_it(self, database):
        conversation = await create_conversation(title="T")
        seeded = await _seed_two_turns(conversation.id)

        removed = await delete_messages_from(conversation.id, seeded["second_reply"].id)

        assert removed == 1
        remaining = await get_messages(conversation.id)
        assert remaining[-1].id == seeded["second_prompt"].id

    async def test_removing_a_turn_takes_its_tool_messages_with_it(self, database):
        # Cutting at the turn's first assistant message must not leave the tool result
        # behind: an orphaned tool message has no call to answer and breaks the request.
        conversation = await create_conversation(title="T")
        seeded = await _seed_two_turns(conversation.id)

        await delete_messages_from(conversation.id, seeded["first_reply"].id)

        remaining = await get_messages(conversation.id)
        assert [message.role for message in remaining] == [MessageRole.USER]
        assert remaining[0].id == seeded["first_prompt"].id

    async def test_a_message_from_another_conversation_is_not_touched(self, database):
        first = await create_conversation(title="A")
        second = await create_conversation(title="B")
        await _seed_two_turns(first.id)
        stranger = await add_message(second.id, MessageRole.USER, content="hello")

        removed = await delete_messages_from(first.id, stranger.id)

        assert removed == 0
        assert len(await get_messages(first.id)) == 6

    async def test_an_unknown_message_removes_nothing(self, database):
        conversation = await create_conversation(title="T")
        await _seed_two_turns(conversation.id)

        assert await delete_messages_from(conversation.id, "no-such-id") == 0
        assert len(await get_messages(conversation.id)) == 6


@pytest.mark.asyncio
class TestPrecedingUserMessage:
    async def test_finds_the_prompt_that_started_a_turn(self, database):
        conversation = await create_conversation(title="T")
        seeded = await _seed_two_turns(conversation.id)

        found = await get_preceding_user_message(conversation.id, seeded["second_reply"].id)

        assert found is not None
        assert found.content == "what is in a.py?"

    async def test_skips_over_assistant_and_tool_messages(self, database):
        conversation = await create_conversation(title="T")
        seeded = await _seed_two_turns(conversation.id)

        # Anchored on the summary, which follows a tool result, not a prompt.
        found = await get_preceding_user_message(conversation.id, seeded["first_summary"].id)

        assert found is not None
        assert found.content == "list the files"

    async def test_returns_nothing_when_no_prompt_precedes(self, database):
        conversation = await create_conversation(title="T")
        first = await add_message(conversation.id, MessageRole.ASSISTANT, content="hi")

        assert await get_preceding_user_message(conversation.id, first.id) is None


@pytest.mark.asyncio
class TestCopyMessagesThrough:
    async def test_copies_the_prefix_into_the_branch(self, database):
        source = await create_conversation(title="T")
        seeded = await _seed_two_turns(source.id)
        branch = await create_conversation(title="T")

        copied = await copy_messages_through(source.id, branch.id, seeded["first_summary"].id)

        assert copied == 4
        messages = await get_messages(branch.id)
        assert [message.content for message in messages] == [
            "list the files",
            "Let me look.",
            "a.py\nb.py",
            "There are two files.",
        ]

    async def test_tool_calls_stay_paired_with_their_results(self, database):
        source = await create_conversation(title="T")
        seeded = await _seed_two_turns(source.id)
        branch = await create_conversation(title="T")

        await copy_messages_through(source.id, branch.id, seeded["first_summary"].id)

        messages = await get_messages(branch.id)
        requested = {
            call["id"]
            for message in messages
            for call in (message.tool_calls or [])
        }
        answered = {message.tool_call_id for message in messages if message.tool_call_id}
        assert requested == answered

    async def test_copies_are_independent_of_the_source(self, database):
        source = await create_conversation(title="T")
        seeded = await _seed_two_turns(source.id)
        branch = await create_conversation(title="T")
        await copy_messages_through(source.id, branch.id, seeded["second_reply"].id)

        # Deleting the source history must leave the branch intact -- that is the whole
        # point of copying rather than referencing.
        await delete_messages_from(source.id, seeded["first_prompt"].id)

        assert len(await get_messages(branch.id)) == 6
        assert await get_messages(source.id) == []

    async def test_copied_messages_get_fresh_identifiers(self, database):
        source = await create_conversation(title="T")
        seeded = await _seed_two_turns(source.id)
        branch = await create_conversation(title="T")
        await copy_messages_through(source.id, branch.id, seeded["second_reply"].id)

        source_ids = {message.id for message in await get_messages(source.id)}
        branch_ids = {message.id for message in await get_messages(branch.id)}
        assert source_ids.isdisjoint(branch_ids)

    async def test_an_unknown_anchor_copies_nothing(self, database):
        source = await create_conversation(title="T")
        await _seed_two_turns(source.id)
        branch = await create_conversation(title="T")

        assert await copy_messages_through(source.id, branch.id, "no-such-id") == 0
        assert await get_messages(branch.id) == []
