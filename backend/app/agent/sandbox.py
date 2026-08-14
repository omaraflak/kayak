"""Docker sandbox lifecycle and command execution.

The docker SDK is entirely synchronous. Every call here is therefore dispatched to a
worker thread: running one inline would block the event loop for the duration of the
container operation, freezing SSE streams and every other conversation on the server
while a single agent waits on a build or a test run.
"""

import asyncio
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, TypeVar
import docker
from docker.errors import NotFound
from backend.app.config import settings
from backend.app.docker_utils import DockerPathResolver

T = TypeVar("T")

# Exit status used by `timeout --signal=KILL` when it terminates the child.
_TIMEOUT_EXIT_CODE = 137


def _build_volume_mounts(workspace_dir: Path) -> Dict[str, Dict[str, str]]:
    """Constructs the container volume mounts dictionary using the DockerPathResolver."""
    workspace_src = DockerPathResolver.resolve_volume_source(workspace_dir)
    volumes_map: Dict[str, Dict[str, str]] = {
        workspace_src: {
            "bind": "/workspace",
            "mode": "rw",
        },
    }

    if settings.DATA_DIR.exists():
        data_src = DockerPathResolver.resolve_volume_source(settings.DATA_DIR)
        if not DockerPathResolver.is_in_container() or not data_src.startswith("/app"):
            volumes_map[data_src] = {
                "bind": "/data",
                "mode": "ro",
            }

    return volumes_map


def _build_custom_tool_runner_script(
    tool_name: str, tool_code: str, arguments: Dict[str, Any]
) -> str:
    """Builds a self-contained Python script to execute a custom tool inside the sandbox."""
    return f"""
import inspect
import json
import sys

# Define tool implementation
{tool_code}


def _resolve_entrypoint():
    named = globals().get('execute') or globals().get('main') or globals().get({tool_name!r})
    if callable(named):
        return named
    # Fall back to the first function *defined in this script*. Filtering on the
    # defining module matters: a bare "first callable" scan picks up imported names
    # such as Path or datetime and calls those instead of the tool.
    for value in globals().values():
        if inspect.isfunction(value) and value.__module__ == '__main__' and not value.__name__.startswith('_'):
            return value
    return None


args = json.loads({json.dumps(arguments)!r})
fn = _resolve_entrypoint()

if not fn:
    print("Error: No callable entrypoint found for tool {tool_name!r}.")
    sys.exit(1)

try:
    result = fn(**args)
    if result is not None:
        print(str(result))
except Exception as e:
    print(f"Error: {{e}}")
    sys.exit(1)
"""


class SandboxShell:
    """An interactive TTY shell running inside a sandbox container.

    Wraps the raw hijacked socket from a `docker exec`. Every method blocks and
    must be called from a worker thread, never the event loop.
    """

    def __init__(self, client: docker.DockerClient, exec_id: str, raw_socket: Any):
        self._client = client
        self._exec_id = exec_id
        # docker-py hands back a SocketIO wrapper; the underlying socket is what
        # supports both directions of the conversation.
        self._sock = raw_socket._sock if hasattr(raw_socket, "_sock") else raw_socket

    def read(self, max_bytes: int = 4096) -> bytes:
        """Reads terminal output; empty bytes means the shell exited."""
        try:
            return self._sock.recv(max_bytes)
        except OSError:
            return b""

    def write(self, data: bytes) -> None:
        """Sends keystrokes to the shell."""
        self._sock.sendall(data)

    def resize(self, rows: int, cols: int) -> None:
        """Matches the PTY size to the client's terminal."""
        try:
            self._client.api.exec_resize(self._exec_id, height=rows, width=cols)
        except Exception:
            # A resize that races the shell exiting is not worth surfacing.
            pass

    def close(self) -> None:
        """Tears the socket down, which also unblocks any pending read."""
        try:
            self._sock.shutdown(2)  # SHUT_RDWR
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass


class SandboxManager:
    """Manages Docker sandbox containers for isolated agent execution."""

    def __init__(self):
        self._client: Optional[docker.DockerClient] = None
        self._docker_available: bool = False
        # Containers started by this process, so shutdown can clean up after itself
        # instead of leaving orphans running until the next manual docker prune.
        self._owned_containers: Set[str] = set()
        self._init_client()

    def _init_client(self):
        try:
            self._client = docker.from_env()
            self._client.ping()
            self._docker_available = True
            DockerPathResolver.initialize(self._client)
        except Exception:
            self._client = None
            self._docker_available = False

    @property
    def is_available(self) -> bool:
        return self._docker_available

    async def _run_blocking(self, func: Callable[[], T]) -> T:
        """Runs a blocking docker SDK call on the default thread pool."""
        return await asyncio.get_running_loop().run_in_executor(None, func)

    def _require_client(self) -> docker.DockerClient:
        """Returns the Docker client or explains why sandboxing is unavailable."""
        if not self._docker_available or not self._client:
            raise RuntimeError(
                "Docker is not available or Docker socket is not mounted."
            )
        return self._client

    def _get_running_container_sync(self, container_id: str) -> Any:
        """Retrieves a Docker container and ensures it is in the running state."""
        client = self._require_client()
        container = client.containers.get(container_id)
        if container.status != "running":
            container.start()
        return container

    @staticmethod
    def _wrap_with_timeout(cmd: List[str], timeout: Optional[int]) -> List[str]:
        """Prefixes a command with `timeout` so a hung process dies inside the container.

        Cancelling the Python-side wait would only abandon the worker thread; the
        process in the container has to be killed where it runs.
        """
        if not timeout or timeout <= 0:
            return cmd
        return ["timeout", "--signal=KILL", str(int(timeout))] + cmd

    @staticmethod
    def _decode_exec_output(exec_result: Any) -> tuple[str, str, int]:
        """Normalizes a demuxed exec result into (stdout, stderr, exit_code)."""
        stdout_bytes, stderr_bytes = exec_result.output
        stdout_str = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr_str = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
        return stdout_str, stderr_str, exec_result.exit_code

    async def create_sandbox(
        self, conversation_id: str, workspace_dir: Path
    ) -> str:
        """Creates and starts an isolated Docker container for a conversation, mounting the workspace.

        Args:
            conversation_id: Conversation identifier.
            workspace_dir: Host directory for conversation workspace.

        Returns:
            str: Container ID.
        """
        client = self._require_client()
        container_name = f"kayak-sandbox-{conversation_id[:8]}"
        volumes_map = _build_volume_mounts(workspace_dir)

        def _create() -> str:
            try:
                existing = client.containers.get(container_name)
                if existing.status != "running":
                    existing.start()
                return existing.id
            except NotFound:
                pass

            image_name = settings.DOCKER_SANDBOX_IMAGE
            try:
                client.images.get(image_name)
            except Exception:
                image_name = "python:3.11-slim"

            container = client.containers.run(
                image=image_name,
                name=container_name,
                command="tail -f /dev/null",  # Keep container alive
                detach=True,
                working_dir="/workspace",
                volumes=volumes_map,
                network_mode="bridge",
                mem_limit="2g",
                cpu_quota=100000,  # 1 CPU
                remove=False,
            )
            return container.id

        container_id = await self._run_blocking(_create)
        self._owned_containers.add(container_id)
        return container_id

    async def ensure_running(self, container_id: str) -> bool:
        """Starts an existing container if it is stopped.

        Used to revive a conversation's recorded container rather than replacing
        it: sub-agent conversations share their parent's container, so creating a
        fresh one per conversation id would silently split them apart.

        Returns:
            bool: True if the container exists and is now running.
        """
        if not self._docker_available or not self._client:
            return False

        def _start() -> bool:
            try:
                self._get_running_container_sync(container_id)
                return True
            except NotFound:
                return False

        try:
            running = await self._run_blocking(_start)
        except Exception:
            return False
        if running:
            self._owned_containers.add(container_id)
        return running

    async def exec_command(
        self, container_id: str, command: str, timeout: Optional[int] = 60
    ) -> str:
        """Executes a command inside the container and returns its combined output."""
        effective_timeout = timeout or settings.SANDBOX_TIMEOUT_SECONDS

        def _exec() -> tuple[str, str, int]:
            container = self._get_running_container_sync(container_id)
            exec_result = container.exec_run(
                cmd=self._wrap_with_timeout(
                    ["/bin/bash", "-c", command], effective_timeout
                ),
                workdir="/workspace",
                demux=True,
            )
            return self._decode_exec_output(exec_result)

        stdout_str, stderr_str, exit_code = await self._run_blocking(_exec)

        if exit_code == _TIMEOUT_EXIT_CODE:
            return (
                f"Error: Command timed out after {effective_timeout} seconds and was"
                " killed. Use start_background_task for long-running work."
            )

        output = []
        if stdout_str:
            output.append(stdout_str)
        if stderr_str:
            output.append(f"STDERR:\n{stderr_str}")
        if exit_code != 0:
            output.append(f"\n[Exit code: {exit_code}]")

        return "\n".join(output) if output else "Command executed with no output."

    async def exec_python(
        self, container_id: str, python_code: str, timeout: Optional[int] = 60
    ) -> str:
        """Executes Python code directly inside the container."""
        effective_timeout = timeout or settings.SANDBOX_TIMEOUT_SECONDS

        def _exec() -> tuple[str, str, int]:
            container = self._get_running_container_sync(container_id)
            exec_result = container.exec_run(
                cmd=self._wrap_with_timeout(
                    ["python3", "-c", python_code], effective_timeout
                ),
                workdir="/workspace",
                demux=True,
            )
            return self._decode_exec_output(exec_result)

        stdout_str, stderr_str, exit_code = await self._run_blocking(_exec)

        if exit_code == _TIMEOUT_EXIT_CODE:
            return f"Error: Execution timed out after {effective_timeout} seconds."
        if exit_code != 0 and stderr_str:
            return f"Error ({exit_code}):\n{stderr_str}"
        return stdout_str or stderr_str or "Execution completed with no output."

    async def exec_custom_tool(
        self,
        container_id: str,
        tool_name: str,
        tool_code: str,
        arguments: Dict[str, Any],
    ) -> str:
        """Executes custom tool python code inside the container sandbox."""
        runner_script = _build_custom_tool_runner_script(
            tool_name=tool_name, tool_code=tool_code, arguments=arguments
        )
        return await self.exec_python(container_id, runner_script)

    async def exec_background_command(
        self, container_id: str, command: str
    ) -> Any:
        """Executes a detached background command in the container."""

        def _exec() -> Any:
            container = self._get_running_container_sync(container_id)
            return container.exec_run(
                cmd=["/bin/bash", "-c", f"nohup {command} > /tmp/task.log 2>&1 &"],
                workdir="/workspace",
                detach=True,
            )

        return await self._run_blocking(_exec)

    async def open_shell(self, container_id: str) -> SandboxShell:
        """Opens an interactive bash session with a real PTY inside the container.

        Returns:
            SandboxShell: Blocking handle for the shell's socket.
        """
        client = self._require_client()

        def _open() -> SandboxShell:
            self._get_running_container_sync(container_id)
            exec_id = client.api.exec_create(
                container_id,
                cmd=["/bin/bash", "-l"],
                stdin=True,
                stdout=True,
                stderr=True,
                tty=True,
                workdir="/workspace",
                environment=["TERM=xterm-256color"],
            )["Id"]
            # tty must be set on start as well as create: without it Docker
            # multiplexes the stream and prefixes every chunk with an 8-byte
            # header, which a terminal renders as stray characters.
            raw_socket = client.api.exec_start(exec_id, socket=True, tty=True)
            return SandboxShell(client, exec_id, raw_socket)

        return await self._run_blocking(_open)

    async def stop_and_remove_sandbox(self, container_id: str):
        """Stops and removes the container."""
        if not self._docker_available or not self._client:
            return

        client = self._client

        def _remove() -> None:
            try:
                container = client.containers.get(container_id)
                container.stop(timeout=2)
                container.remove(v=True, force=True)
            except Exception:
                pass

        await self._run_blocking(_remove)
        self._owned_containers.discard(container_id)

    async def shutdown_all(self) -> None:
        """Stops every sandbox container this process started."""
        for container_id in list(self._owned_containers):
            await self.stop_and_remove_sandbox(container_id)


sandbox_manager = SandboxManager()
