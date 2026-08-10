import asyncio
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from backend.app.agent.sandbox import sandbox_manager
from backend.app.database import (
    append_task_output,
    create_task,
    get_task,
    list_tasks,
    update_task,
)
from backend.app.models import BackgroundTask, TaskStatus, TaskType


class TaskManager:
    """Manages asynchronous long-running background processes and sub-agent executions."""

    def __init__(self):
        # Maps task_id -> running asyncio subprocess or task
        self._running_processes: Dict[str, asyncio.subprocess.Process] = {}
        self._running_async_tasks: Dict[str, asyncio.Task[Any]] = {}
        self._listeners: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}

    def add_listener(
        self, conversation_id: str, listener: Callable[[Dict[str, Any]], None]
    ) -> None:
        """Subscribes an event callback listener to live task events for a conversation.

        Args:
            conversation_id: Unique conversation identifier.
            listener: Callback taking an event dictionary.
        """
        if conversation_id not in self._listeners:
            self._listeners[conversation_id] = []
        self._listeners[conversation_id].append(listener)

    def remove_listener(
        self, conversation_id: str, listener: Callable[[Dict[str, Any]], None]
    ) -> None:
        """Unsubscribes a listener callback.

        Args:
            conversation_id: Unique conversation identifier.
            listener: The callback to remove.
        """
        if conversation_id in self._listeners:
            if listener in self._listeners[conversation_id]:
                self._listeners[conversation_id].remove(listener)

    def notify_listeners(self, conversation_id: str, event: Dict[str, Any]) -> None:
        """Dispatches an event to all subscribed listeners for a conversation.

        Args:
            conversation_id: Unique conversation identifier.
            event: Event payload.
        """
        if conversation_id in self._listeners:
            for listener in list(self._listeners[conversation_id]):
                try:
                    listener(event)
                except Exception:
                    pass

    async def start_shell_task(
        self,
        conversation_id: str,
        name: str,
        command: str,
        workspace_dir: Optional[Path] = None,
        container_id: Optional[str] = None,
    ) -> BackgroundTask:
        """Starts an asynchronous long-running background shell command.

        Args:
            conversation_id: ID of the originating conversation.
            name: Human-readable task name.
            command: Shell command line to execute.
            workspace_dir: Working directory for process execution.
            container_id: Optional Docker container identifier.

        Returns:
            BackgroundTask: Created task record in RUNNING status.
        """
        task = await create_task(
            conversation_id=conversation_id,
            task_type=TaskType.SHELL_COMMAND,
            name=name,
            command=command,
        )

        cwd = workspace_dir if workspace_dir else Path.cwd()

        async def _run_process():
            try:
                # If running inside a container
                if container_id:
                    # Execute in background inside container
                    proc = await sandbox_manager.exec_background_command(
                        container_id=container_id, command=command
                    )
                    await update_task(task.id, status=TaskStatus.RUNNING)
                    self.notify_listeners(
                        conversation_id,
                        {
                            "type": "task_started",
                            "task_id": task.id,
                            "name": name,
                        },
                    )
                    return

                # Local process
                proc = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    stdin=asyncio.subprocess.PIPE,
                    cwd=str(cwd),
                )
                self._running_processes[task.id] = proc
                await update_task(task.id, pid=proc.pid, status=TaskStatus.RUNNING)

                self.notify_listeners(
                    conversation_id,
                    {
                        "type": "task_started",
                        "task_id": task.id,
                        "name": name,
                        "pid": proc.pid,
                    },
                )

                async def _read_stream(stream: Optional[asyncio.StreamReader], is_stderr: bool = False):
                    if not stream:
                        return
                    while True:
                        line = await stream.readline()
                        if not line:
                            break
                        text = line.decode("utf-8", errors="replace")
                        if is_stderr:
                            await append_task_output(
                                task.id, stderr_chunk=text
                            )
                        else:
                            await append_task_output(
                                task.id, stdout_chunk=text
                            )

                        self.notify_listeners(
                            conversation_id,
                            {
                                "type": "task_output",
                                "task_id": task.id,
                                "stream": "stderr" if is_stderr else "stdout",
                                "text": text,
                            },
                        )

                await asyncio.gather(
                    _read_stream(proc.stdout, is_stderr=False),
                    _read_stream(proc.stderr, is_stderr=True),
                )

                exit_code = await proc.wait()
                final_status = TaskStatus.COMPLETED if exit_code == 0 else TaskStatus.FAILED
                await update_task(
                    task.id, status=final_status, exit_code=exit_code
                )

                self.notify_listeners(
                    conversation_id,
                    {
                        "type": "task_finished",
                        "task_id": task.id,
                        "name": name,
                        "status": final_status.value,
                        "exit_code": exit_code,
                    },
                )

            except Exception as e:
                await update_task(task.id, status=TaskStatus.FAILED, stderr=str(e))
                self.notify_listeners(
                    conversation_id,
                    {
                        "type": "task_finished",
                        "task_id": task.id,
                        "name": name,
                        "status": TaskStatus.FAILED.value,
                        "error": str(e),
                    },
                )
            finally:
                self._running_processes.pop(task.id, None)

        async_task = asyncio.create_task(_run_process())
        self._running_async_tasks[task.id] = async_task
        return task

    async def send_input(self, task_id: str, input_text: str) -> bool:
        """Sends stdin input data to a running background process.

        Args:
            task_id: Unique task identifier.
            input_text: Text payload to stream to stdin.

        Returns:
            bool: True if sent successfully, False otherwise.
        """
        proc = self._running_processes.get(task_id)
        if proc and proc.stdin and not proc.stdin.is_closing():
            if not input_text.endswith("\n"):
                input_text += "\n"
            proc.stdin.write(input_text.encode("utf-8"))
            await proc.stdin.drain()
            return True
        return False

    async def stop_task(self, task_id: str) -> bool:
        """Terminates a running background task process or async coroutine.

        Args:
            task_id: Unique task identifier.

        Returns:
            bool: True if stopped, False if not found.
        """
        proc = self._running_processes.get(task_id)
        if proc:
            try:
                proc.terminate()
                await asyncio.sleep(0.5)
                if proc.returncode is None:
                    proc.kill()
                await update_task(task_id, status=TaskStatus.STOPPED)
                return True
            except Exception:
                pass

        async_task = self._running_async_tasks.get(task_id)
        if async_task and not async_task.done():
            async_task.cancel()
            await update_task(task_id, status=TaskStatus.STOPPED)
            return True

        return False


task_manager = TaskManager()
