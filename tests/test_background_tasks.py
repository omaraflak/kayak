"""Tests for background tasks running inside a conversation's container.

Every conversation runs in Docker, so this is the path every background task takes.
It used to detach the process into a fixed log file inside the container that nothing
ever read: the task reported no output, never recorded an exit code, stayed "running"
for the rest of the conversation's life, and could not be stopped.
"""

from pathlib import Path
from typing import AsyncIterator, Dict, List, Optional

import pytest

from backend.app.agent import task_manager as task_manager_module
from backend.app.agent.events import TaskEvent
from backend.app.agent.sandbox import ExecChunk
from backend.app.agent.task_manager import TaskManager
from backend.app.config import settings
from backend.app.database import create_conversation, get_task, init_db
from backend.app.models import TaskStatus


class FakeSandbox:
    """Stands in for the Docker sandbox, scripted with the output of one exec."""

    def __init__(self, chunks: List[ExecChunk], exit_code: Optional[int] = 0) -> None:
        self._chunks = chunks
        self._exit_code = exit_code
        self.started: List[Dict[str, str]] = []
        self.killed: List[str] = []

    async def start_background_command(
        self, container_id: str, command: str, task_id: str
    ) -> str:
        self.started.append(
            {"container_id": container_id, "command": command, "task_id": task_id}
        )
        return f"exec-for-{task_id}"

    async def stream_exec_output(self, exec_id: str) -> AsyncIterator[ExecChunk]:
        for chunk in self._chunks:
            yield chunk

    async def exec_exit_code(self, exec_id: str) -> Optional[int]:
        return self._exit_code

    async def kill_background_command(self, container_id: str, task_id: str) -> bool:
        self.killed.append(task_id)
        return True


@pytest.fixture
async def conversation_id(tmp_path: Path, monkeypatch) -> str:
    monkeypatch.setattr(settings, "DB_PATH", tmp_path / "tasks.db")
    await init_db()
    conversation = await create_conversation(
        title="t", agent_id="general", isolated_container=True
    )
    return conversation.id


async def _run_to_completion(manager: TaskManager, task_id: str) -> None:
    """Waits for the manager's own task body to finish."""
    running = manager._running.get(task_id)
    if running is not None:
        await running


class TestContainerTaskLifecycle:
    async def test_output_is_recorded(self, conversation_id, monkeypatch):
        sandbox = FakeSandbox(
            [ExecChunk("building...\n", False), ExecChunk("done\n", False)]
        )
        monkeypatch.setattr(task_manager_module, "sandbox_manager", sandbox)
        manager = TaskManager()

        task = await manager.start_shell_task(
            conversation_id=conversation_id,
            name="build",
            command="make",
            container_id="container-1",
        )
        await _run_to_completion(manager, task.id)

        stored = await get_task(task.id)
        assert stored is not None
        assert stored.stdout == "building...\ndone\n"

    async def test_stderr_is_kept_separate(self, conversation_id, monkeypatch):
        sandbox = FakeSandbox(
            [ExecChunk("ok\n", False), ExecChunk("warning: deprecated\n", True)]
        )
        monkeypatch.setattr(task_manager_module, "sandbox_manager", sandbox)
        manager = TaskManager()

        task = await manager.start_shell_task(
            conversation_id=conversation_id, name="build", command="make",
            container_id="container-1",
        )
        await _run_to_completion(manager, task.id)

        stored = await get_task(task.id)
        assert stored is not None
        assert stored.stdout == "ok\n"
        assert stored.stderr == "warning: deprecated\n"

    async def test_a_finished_task_stops_being_running(self, conversation_id, monkeypatch):
        monkeypatch.setattr(
            task_manager_module, "sandbox_manager", FakeSandbox([], exit_code=0)
        )
        manager = TaskManager()

        task = await manager.start_shell_task(
            conversation_id=conversation_id, name="build", command="true",
            container_id="container-1",
        )
        await _run_to_completion(manager, task.id)

        stored = await get_task(task.id)
        assert stored is not None
        assert stored.status == TaskStatus.COMPLETED
        assert stored.exit_code == 0

    async def test_a_failing_command_is_reported_as_failed(self, conversation_id, monkeypatch):
        monkeypatch.setattr(
            task_manager_module, "sandbox_manager", FakeSandbox([], exit_code=2)
        )
        manager = TaskManager()

        task = await manager.start_shell_task(
            conversation_id=conversation_id, name="build", command="false",
            container_id="container-1",
        )
        await _run_to_completion(manager, task.id)

        stored = await get_task(task.id)
        assert stored is not None
        assert stored.status == TaskStatus.FAILED
        assert stored.exit_code == 2

    async def test_the_command_runs_in_the_conversation_container(
        self, conversation_id, monkeypatch
    ):
        sandbox = FakeSandbox([])
        monkeypatch.setattr(task_manager_module, "sandbox_manager", sandbox)
        manager = TaskManager()

        task = await manager.start_shell_task(
            conversation_id=conversation_id, name="serve", command="npm run dev",
            container_id="container-1",
        )
        await _run_to_completion(manager, task.id)

        assert sandbox.started == [
            {"container_id": "container-1", "command": "npm run dev", "task_id": task.id}
        ]


class TestStopping:
    async def test_stopping_kills_the_process_in_the_container(
        self, conversation_id, monkeypatch
    ):
        # Cancelling the reader would leave the process alive with nothing watching it,
        # which is why the kill has to happen inside the container.
        sandbox = FakeSandbox([ExecChunk("serving\n", False)])
        monkeypatch.setattr(task_manager_module, "sandbox_manager", sandbox)
        manager = TaskManager()

        task = await manager.start_shell_task(
            conversation_id=conversation_id, name="serve", command="sleep 999",
            container_id="container-1",
        )
        stopped = await manager.stop_task(task.id)
        await _run_to_completion(manager, task.id)

        assert stopped is True
        assert sandbox.killed == [task.id]
        stored = await get_task(task.id)
        # Still "stopped" after the process exits: killing it makes it exit non-zero,
        # which must not be reported back as a failure the user did not cause.
        assert stored is not None and stored.status == TaskStatus.STOPPED

    async def test_stopping_an_unknown_task_reports_failure(self, conversation_id):
        manager = TaskManager()

        assert await manager.stop_task("no-such-task") is False


class TestListeners:
    async def test_subscribers_see_output_as_it_arrives(self, conversation_id, monkeypatch):
        monkeypatch.setattr(
            task_manager_module,
            "sandbox_manager",
            FakeSandbox([ExecChunk("line one\n", False)]),
        )
        manager = TaskManager()
        seen: List[TaskEvent] = []
        manager.add_listener(conversation_id, seen.append)

        task = await manager.start_shell_task(
            conversation_id=conversation_id, name="build", command="make",
            container_id="container-1",
        )
        await _run_to_completion(manager, task.id)

        kinds = [event["type"] for event in seen]
        assert kinds == ["task_started", "task_output", "task_finished"]
        assert seen[1]["text"] == "line one\n"
        assert seen[1]["stream"] == "stdout"

    async def test_a_removed_listener_stops_hearing(self, conversation_id, monkeypatch):
        monkeypatch.setattr(
            task_manager_module, "sandbox_manager", FakeSandbox([])
        )
        manager = TaskManager()
        seen: List[TaskEvent] = []
        manager.add_listener(conversation_id, seen.append)
        manager.remove_listener(conversation_id, seen.append)

        task = await manager.start_shell_task(
            conversation_id=conversation_id, name="build", command="make",
            container_id="container-1",
        )
        await _run_to_completion(manager, task.id)

        assert seen == []
