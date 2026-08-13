"""Tests for message ordering, interrupted-state recovery, and cascade deletes."""

import asyncio
from pathlib import Path
import pytest
from backend.app.config import settings
from backend.app.database import (
    add_message,
    create_conversation,
    delete_conversation,
    get_conversation,
    get_messages,
    init_db,
    list_child_conversations,
    reconcile_interrupted_state,
    update_conversation,
)
from backend.app.models import ConversationStatus, MessageRole


@pytest.fixture
async def database(tmp_path: Path, monkeypatch):
    """Points the data layer at a fresh database for each test."""
    monkeypatch.setattr(settings, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(settings, "WORKSPACES_DIR", tmp_path / "workspaces")
    await init_db()
    yield


@pytest.mark.asyncio
async def test_messages_return_in_insertion_order(database):
    conversation = await create_conversation(title="t")

    # Written back-to-back, these can land on identical ISO timestamps; ordering has
    # to stay stable regardless, or a tool result can sort ahead of its own call.
    for index in range(50):
        await add_message(
            conversation_id=conversation.id,
            role=MessageRole.USER,
            content=str(index),
        )

    messages = await get_messages(conversation.id)
    assert [m.content for m in messages] == [str(i) for i in range(50)]


@pytest.mark.asyncio
async def test_concurrent_writes_preserve_a_total_order(database):
    conversation = await create_conversation(title="t")

    await asyncio.gather(
        *[
            add_message(
                conversation_id=conversation.id,
                role=MessageRole.USER,
                content=f"m{index}",
            )
            for index in range(20)
        ]
    )

    messages = await get_messages(conversation.id)
    assert len(messages) == 20
    assert len({m.id for m in messages}) == 20


@pytest.mark.asyncio
async def test_reconcile_clears_conversations_stuck_in_running(database):
    conversation = await create_conversation(title="t")
    await update_conversation(conversation.id, status=ConversationStatus.RUNNING)

    await reconcile_interrupted_state()

    restored = await get_conversation(conversation.id)
    assert restored is not None
    assert restored.status == ConversationStatus.ACTIVE


@pytest.mark.asyncio
async def test_subagent_container_is_persisted(database):
    parent = await create_conversation(title="parent", container_id="abc123")
    child = await create_conversation(
        title="child",
        container_id="abc123",
        parent_conversation_id=parent.id,
    )

    # Reading the child back is the case that used to lose the sandbox: the container
    # was only ever set on the in-memory object.
    reloaded = await get_conversation(child.id)
    assert reloaded is not None
    assert reloaded.container_id == "abc123"
    assert reloaded.parent_conversation_id == parent.id


@pytest.mark.asyncio
async def test_children_are_discoverable_from_the_parent(database):
    parent = await create_conversation(title="parent")
    await create_conversation(title="c1", parent_conversation_id=parent.id)
    await create_conversation(title="c2", parent_conversation_id=parent.id)
    await create_conversation(title="unrelated")

    children = await list_child_conversations(parent.id)
    assert sorted(c.title for c in children) == ["c1", "c2"]


@pytest.mark.asyncio
async def test_deleting_a_conversation_cascades_to_its_messages(database):
    conversation = await create_conversation(title="t")
    await add_message(
        conversation_id=conversation.id, role=MessageRole.USER, content="hi"
    )

    await delete_conversation(conversation.id)

    assert await get_messages(conversation.id) == []
