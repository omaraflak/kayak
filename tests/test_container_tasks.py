"""Tests for the tasks belonging to a conversation's container.

Background tasks are no longer a page of their own: a task is a process running in
one conversation's container, so it is listed in that conversation's container drawer.
Two things have to hold for that view to be honest -- a sub-agent task must name the
conversation whose transcript it is, and a conversation's list must include the
processes its sub-agents left running, since they run in the same container.
"""

import pytest

from backend.app.database import (
    create_conversation,
    create_task,
    init_db,
    list_tasks,
)


async def _conversation(title: str, parent: str | None = None) -> str:
    conversation = await create_conversation(
        title=title,
        agent_id="general",
        isolated_container=True,
        parent_conversation_id=parent,
    )
    return conversation.id


class TestSubagentTaskLink:
    async def test_a_subagent_task_names_its_conversation(self):
        await init_db()
        parent = await _conversation("parent")
        child = await _conversation("child", parent=parent)

        task = await create_task(
            conversation_id=parent,
            task_type="subagent",
            name="SubAgent [general]",
            command="do the thing",
            subagent_conversation_id=child,
        )

        assert task.subagent_conversation_id == child
        stored = [t for t in await list_tasks(conversation_id=parent) if t.id == task.id]
        assert stored[0].subagent_conversation_id == child

    async def test_a_shell_task_has_no_conversation_of_its_own(self):
        await init_db()
        conversation = await _conversation("solo")

        task = await create_task(
            conversation_id=conversation,
            task_type="shell_command",
            name="dev server",
            command="npm run dev",
        )

        assert task.subagent_conversation_id is None


class TestConversationTaskTree:
    async def test_subagent_tasks_are_included(self):
        await init_db()
        parent = await _conversation("parent")
        child = await _conversation("child", parent=parent)

        await create_task(
            conversation_id=parent, task_type="shell_command", name="parent server"
        )
        # Started by the sub-agent, but running in the parent's container: hiding it
        # would leave a process nobody could find, let alone stop.
        await create_task(
            conversation_id=child, task_type="shell_command", name="child build"
        )

        names = {
            task.name
            for task in await list_tasks(conversation_id=parent, include_subagents=True)
        }

        assert names == {"parent server", "child build"}

    async def test_nested_subagents_are_reached_at_any_depth(self):
        await init_db()
        top = await _conversation("top")
        middle = await _conversation("middle", parent=top)
        bottom = await _conversation("bottom", parent=middle)

        await create_task(
            conversation_id=bottom, task_type="shell_command", name="deep task"
        )

        names = {
            task.name for task in await list_tasks(conversation_id=top, include_subagents=True)
        }

        assert "deep task" in names

    async def test_an_unrelated_conversation_is_not_included(self):
        await init_db()
        mine = await _conversation("mine")
        theirs = await _conversation("theirs")

        await create_task(
            conversation_id=theirs, task_type="shell_command", name="not mine"
        )

        names = {
            task.name for task in await list_tasks(conversation_id=mine, include_subagents=True)
        }

        assert "not mine" not in names

    async def test_the_parent_is_not_reached_from_the_child(self):
        # The tree runs downwards only. A sub-agent's own drawer shows its work, not
        # everything its parent is doing.
        await init_db()
        parent = await _conversation("parent")
        child = await _conversation("child", parent=parent)

        await create_task(
            conversation_id=parent, task_type="shell_command", name="parent task"
        )

        names = {
            task.name for task in await list_tasks(conversation_id=child, include_subagents=True)
        }

        assert names == set()

    async def test_the_filter_still_scopes_to_one_conversation_when_asked(self):
        await init_db()
        parent = await _conversation("parent")
        child = await _conversation("child", parent=parent)
        await create_task(conversation_id=child, task_type="shell_command", name="child task")

        names = {task.name for task in await list_tasks(conversation_id=parent)}

        assert names == set()
