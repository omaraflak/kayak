"""Long-running processes started by an agent, and the sub-agent runs it delegates.

A task outlives the tool call that started it, so it needs somewhere to live, a way to
report progress while it runs, and a way to be stopped. Everything here is keyed on the
conversation that owns the container the work runs in.
"""

import asyncio
import logging
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional, Set

from backend.app.agent.events import TaskEvent, task_finished, task_output, task_started
from backend.app.agent.sandbox import sandbox_manager
from backend.app.database import append_task_output, create_task, update_task
from backend.app.models import BackgroundTask, TaskStatus, TaskType

logger = logging.getLogger(__name__)

TaskListener = Callable[[TaskEvent], None]

#: How often a streamed task's output is flushed to the database. Writing every chunk
#: turns a chatty build into thousands of small transactions.
_FLUSH_INTERVAL_SECONDS = 0.5


class _OutputBuffer:
    """Accumulates a stream's output and flushes it on an interval.

    Keeps the database write rate bounded without making the live stream any slower:
    subscribers see every chunk immediately, storage catches up shortly after.
    """

    def __init__(self, task_id: str) -> None:
        self._task_id = task_id
        self._stdout: List[str] = []
        self._stderr: List[str] = []
        self._last_flush = 0.0

    def add(self, text: str, is_stderr: bool) -> None:
        (self._stderr if is_stderr else self._stdout).append(text)

    @property
    def is_empty(self) -> bool:
        return not self._stdout and not self._stderr

    async def flush(self) -> None:
        if self.is_empty:
            return
        stdout_chunk = "".join(self._stdout)
        stderr_chunk = "".join(self._stderr)
        self._stdout.clear()
        self._stderr.clear()
        await append_task_output(
            self._task_id,
            stdout_chunk=stdout_chunk or None,
            stderr_chunk=stderr_chunk or None,
        )


class TaskManager:
    """Runs and tracks background processes and sub-agent executions."""

    def __init__(self) -> None:
        self._running: Dict[str, asyncio.Task[None]] = {}
        #: Host processes, for the cases that do not run in a container.
        self._processes: Dict[str, asyncio.subprocess.Process] = {}
        #: Container a task is running in, so it can be killed where it lives.
        self._containers: Dict[str, str] = {}
        #: Tasks the user asked to stop. Killing a process makes it exit non-zero a
        #: moment later, and without this the run would then be recorded as "failed"
        #: -- reporting a deliberate stop as a fault.
        self._stopping: Set[str] = set()
        self._listeners: Dict[str, List[TaskListener]] = {}

    def register_run(self, task_id: str, run: "asyncio.Task[str]") -> None:
        """Tracks work started elsewhere, so stop_task can reach it.

        Sub-agent runs are driven by the sub-agent tool rather than by this class.
        Without registering them, stopping a sub-agent task found nothing to cancel
        and quietly reported failure.
        """
        self._running[task_id] = run  # type: ignore[assignment]
        run.add_done_callback(lambda _: self._running.pop(task_id, None))

    # ---------------------------------------------------------------- listeners

    def add_listener(self, conversation_id: str, listener: TaskListener) -> None:
        """Subscribes to live task events for a conversation."""
        self._listeners.setdefault(conversation_id, []).append(listener)

    def remove_listener(self, conversation_id: str, listener: TaskListener) -> None:
        """Unsubscribes a listener."""
        listeners = self._listeners.get(conversation_id)
        if not listeners:
            return
        if listener in listeners:
            listeners.remove(listener)
        if not listeners:
            self._listeners.pop(conversation_id, None)

    def notify_listeners(self, conversation_id: str, event: TaskEvent) -> None:
        """Dispatches an event to every listener for a conversation."""
        for listener in list(self._listeners.get(conversation_id, [])):
            try:
                listener(event)
            except Exception:
                logger.debug("A task listener raised; dropping the event for it.",
                             exc_info=True)

    # ------------------------------------------------------------------- shell

    async def start_shell_task(
        self,
        conversation_id: str,
        name: str,
        command: str,
        workspace_dir: Optional[Path] = None,
        container_id: Optional[str] = None,
    ) -> BackgroundTask:
        """Starts a long-running shell command and returns its task record.

        Args:
            conversation_id: Conversation that owns the task.
            name: Human-readable label.
            command: Shell command line to execute.
            workspace_dir: Working directory, when running outside a container.
            container_id: Container to run in. Every conversation has one; the
                directory path is only used by tests and tooling that do not.

        Returns:
            BackgroundTask: The created record, already running.
        """
        task = await create_task(
            conversation_id=conversation_id,
            task_type=TaskType.SHELL_COMMAND,
            name=name,
            command=command,
        )
        if container_id:
            self._containers[task.id] = container_id

        runner = self._run_in_container(task, command, container_id) if container_id \
            else self._run_on_host(task, command, workspace_dir or Path.cwd())

        self._spawn(task.id, self._guarded(task, name, runner))
        return task

    def _spawn(self, task_id: str, coroutine: Awaitable[None]) -> None:
        """Runs a task's body, keeping a reference so it is not collected mid-flight."""
        running = asyncio.ensure_future(coroutine)
        self._running[task_id] = running
        running.add_done_callback(lambda _: self._running.pop(task_id, None))

    async def _guarded(
        self, task: BackgroundTask, name: str, body: Awaitable[None]
    ) -> None:
        """Reports a task's failure or cancellation instead of losing it."""
        try:
            await body
        except asyncio.CancelledError:
            await update_task(task.id, status=TaskStatus.STOPPED)
            self.notify_listeners(
                task.conversation_id, task_finished(task.id, name, TaskStatus.STOPPED)
            )
            raise
        except Exception as error:
            logger.exception("Background task %s failed", task.id)
            await update_task(task.id, status=TaskStatus.FAILED, stderr=str(error))
            self.notify_listeners(
                task.conversation_id,
                task_finished(task.id, name, TaskStatus.FAILED, error=str(error)),
            )
        finally:
            self._containers.pop(task.id, None)

    async def _run_in_container(
        self, task: BackgroundTask, command: str, container_id: str
    ) -> None:
        """Streams a command running inside the conversation's container."""
        exec_id = await sandbox_manager.start_background_command(
            container_id=container_id, command=command, task_id=task.id
        )
        await update_task(task.id, status=TaskStatus.RUNNING)
        self.notify_listeners(task.conversation_id, task_started(task.id, task.name))

        buffer = _OutputBuffer(task.id)
        deadline = asyncio.get_running_loop().time() + _FLUSH_INTERVAL_SECONDS

        async for chunk in sandbox_manager.stream_exec_output(exec_id):
            buffer.add(chunk.text, chunk.is_stderr)
            self.notify_listeners(
                task.conversation_id,
                task_output(task.id, chunk.text, is_stderr=chunk.is_stderr),
            )
            now = asyncio.get_running_loop().time()
            if now >= deadline:
                await buffer.flush()
                deadline = now + _FLUSH_INTERVAL_SECONDS

        await buffer.flush()
        exit_code = await sandbox_manager.exec_exit_code(exec_id)
        await self._finish(task, exit_code)

    async def _run_on_host(
        self, task: BackgroundTask, command: str, cwd: Path
    ) -> None:
        """Streams a command running directly on this machine."""
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE,
            cwd=str(cwd),
        )
        self._processes[task.id] = process
        await update_task(task.id, pid=process.pid, status=TaskStatus.RUNNING)
        self.notify_listeners(
            task.conversation_id, task_started(task.id, task.name, pid=process.pid)
        )

        buffer = _OutputBuffer(task.id)
        await asyncio.gather(
            self._pump(process.stdout, task, buffer, is_stderr=False),
            self._pump(process.stderr, task, buffer, is_stderr=True),
        )
        await buffer.flush()
        await self._finish(task, await process.wait())
        self._processes.pop(task.id, None)

    async def _pump(
        self,
        stream: Optional[asyncio.StreamReader],
        task: BackgroundTask,
        buffer: _OutputBuffer,
        is_stderr: bool,
    ) -> None:
        """Forwards one of a process's streams to subscribers and to storage."""
        if not stream:
            return
        while True:
            line = await stream.readline()
            if not line:
                return
            text = line.decode("utf-8", errors="replace")
            buffer.add(text, is_stderr)
            self.notify_listeners(
                task.conversation_id, task_output(task.id, text, is_stderr=is_stderr)
            )
            await buffer.flush()

    async def _finish(self, task: BackgroundTask, exit_code: Optional[int]) -> None:
        """Records how a task ended and announces it."""
        was_stopped = task.id in self._stopping
        self._stopping.discard(task.id)
        if was_stopped:
            status = TaskStatus.STOPPED
        else:
            status = TaskStatus.COMPLETED if exit_code == 0 else TaskStatus.FAILED
        await update_task(task.id, status=status, exit_code=exit_code)
        self.notify_listeners(
            task.conversation_id,
            task_finished(task.id, task.name, status, exit_code=exit_code),
        )

    # ------------------------------------------------------------------ control

    async def send_input(self, task_id: str, input_text: str) -> bool:
        """Writes to a running host process's stdin.

        Returns:
            bool: False if the task has no stdin to write to, which includes every
            task running inside a container.
        """
        process = self._processes.get(task_id)
        if not process or not process.stdin or process.stdin.is_closing():
            return False
        if not input_text.endswith("\n"):
            input_text += "\n"
        process.stdin.write(input_text.encode("utf-8"))
        await process.stdin.drain()
        return True

    async def stop_task(self, task_id: str) -> bool:
        """Terminates a running task.

        A container task is killed where it runs: cancelling the coroutine that reads
        its output would only stop the reading, leaving the process alive with nothing
        watching it.

        Returns:
            bool: True if something was stopped.
        """
        container_id = self._containers.get(task_id)
        if container_id:
            self._stopping.add(task_id)
            killed = await sandbox_manager.kill_background_command(container_id, task_id)
            if killed:
                # The stream ends on its own now; the status is recorded here so a
                # process that ignores the signal still shows as stopped.
                await update_task(task_id, status=TaskStatus.STOPPED)
            else:
                self._stopping.discard(task_id)
            return killed

        process = self._processes.get(task_id)
        if process:
            self._stopping.add(task_id)
            process.terminate()
            await asyncio.sleep(0.5)
            if process.returncode is None:
                process.kill()
            await update_task(task_id, status=TaskStatus.STOPPED)
            return True

        running = self._running.get(task_id)
        if running and not running.done():
            running.cancel()
            return True

        return False


task_manager = TaskManager()
