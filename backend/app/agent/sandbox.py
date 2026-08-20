"""Docker sandbox lifecycle and command execution.

The docker SDK is entirely synchronous. Every call here is therefore dispatched to a
worker thread: running one inline would block the event loop for the duration of the
container operation, freezing SSE streams and every other conversation on the server
while a single agent waits on a build or a test run.
"""

import asyncio
import json
from pathlib import Path
import queue
import threading
import time
from typing import (
    Any,
    AsyncIterator,
    Callable,
    Dict,
    Iterator,
    List,
    Optional,
    Set,
    Tuple,
    TypeVar,
)
import docker
from docker.errors import NotFound
from backend.app.agent.python_repl import (
    PYTHON_REPL_DRIVER,
    demux_docker_frames,
    format_execution_result,
    scan_for_response,
)
from backend.app.config import settings
from backend.app.docker_utils import DockerPathResolver

T = TypeVar("T")

# Exit status used by `timeout --signal=KILL` when it terminates the child.
_TIMEOUT_EXIT_CODE = 137

#: Interactive shell setup for the user-facing terminal: a colored prompt and
#: color-enabled core utilities, which the slim images do not configure.
_SHELL_RCFILE = """\
export TERM=xterm-256color
alias ls='ls --color=auto'
alias grep='grep --color=auto'
PS1='\\[\\e[1;36m\\]container\\[\\e[0m\\]:\\[\\e[1;34m\\]\\w\\[\\e[0m\\]$ '
"""


#: Environment variable stamped onto a background process so it can be found and
#: killed later. A container has no process supervisor, and the exec API gives no
#: handle that survives the call, so the marker is the only way back to the process.
BACKGROUND_TASK_MARKER = "KAYAK_TASK_ID"


#: Every sandbox is named from this, which is also how they are found again
#: after a restart -- the set of containers this process started is empty then,
#: and the ones from the previous run still need stopping.
SANDBOX_NAME_PREFIX = "kayak-sandbox-"


def _shell_quote(text: str) -> str:
    """Single-quotes a string for safe embedding in a POSIX shell command."""
    return "'" + text.replace("'", "'\\''") + "'"


class ExecChunk:
    """One piece of output from a streaming exec."""

    __slots__ = ("text", "is_stderr")

    def __init__(self, text: str, is_stderr: bool) -> None:
        self.text = text
        self.is_stderr = is_stderr


def _build_volume_mounts(workspace_dir: Path) -> Dict[str, Dict[str, str]]:
    """Constructs the container volume mounts dictionary using the DockerPathResolver.

    Only the conversation's own workspace is mounted -- nothing else. The whole
    platform data directory used to be exposed read-only at `/data`, which let
    every agent read every other conversation's files, the conversation
    database, and settings.json with its plaintext provider API keys. Nothing
    in the sandbox execution path ever needed that mount.
    """
    workspace_src = DockerPathResolver.resolve_volume_source(workspace_dir)
    return {
        workspace_src: {
            "bind": "/workspace",
            "mode": "rw",
        },
    }


def has_forbidden_data_mount(container: Any) -> bool:
    """Detects the legacy `/data` mount on a container built before its removal.

    Containers are long-lived and reused across app restarts, so without this
    check an already-created sandbox would keep leaking other conversations'
    files (and the API keys) until it happened to be deleted.
    """
    return any(
        mount.get("Destination") == "/data"
        for mount in container.attrs.get("Mounts", [])
    )


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


class PythonSession:
    """A persistent Python interpreter running inside a sandbox container.

    Wraps the stdin/stdout socket of the REPL driver exec. Everything here
    blocks and must run on a worker thread.
    """

    def __init__(self, raw_socket: Any):
        self._sock = raw_socket._sock if hasattr(raw_socket, "_sock") else raw_socket
        self._raw = b""
        self._text = ""
        self.dead = False

    def execute(self, code: str, timeout: int) -> str:
        """Runs code in the shared interpreter and returns its output.

        Raises:
            TimeoutError: If no response arrives in time. The session must be
                discarded afterwards: the interpreter is still busy, and a later
                request would receive this one's answer.
            ConnectionError: If the interpreter process has exited.
        """
        request = json.dumps({"code": code}) + "\n"
        try:
            self._sock.sendall(request.encode("utf-8"))
        except OSError as error:
            self.dead = True
            raise ConnectionError(str(error))

        deadline = time.monotonic() + timeout
        leaked = ""
        while True:
            response, newly_leaked, self._text = scan_for_response(self._text)
            leaked += newly_leaked
            if response is not None:
                return format_execution_result(leaked, response)

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.dead = True
                raise TimeoutError(f"no response after {timeout} seconds")

            self._sock.settimeout(remaining)
            try:
                chunk = self._sock.recv(65536)
            except TimeoutError:
                self.dead = True
                raise
            except OSError as error:
                self.dead = True
                raise ConnectionError(str(error))
            if not chunk:
                self.dead = True
                raise ConnectionError("the interpreter process exited")

            self._raw += chunk
            payload, self._raw = demux_docker_frames(self._raw)
            self._text += payload.decode("utf-8", errors="replace")

    def close(self) -> None:
        try:
            self._sock.shutdown(2)  # SHUT_RDWR
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass
        self.dead = True


class SandboxManager:
    """Manages Docker sandbox containers for isolated agent execution."""

    def __init__(self):
        self._client: Optional[docker.DockerClient] = None
        self._docker_available: bool = False
        # Containers started by this process, so shutdown can clean up after itself
        # instead of leaving orphans running until the next manual docker prune.
        self._owned_containers: Set[str] = set()
        # One persistent Python interpreter per container, created on first use, and
        # one lock per container guarding it.
        self._python_sessions: Dict[str, PythonSession] = {}
        self._python_locks: Dict[str, asyncio.Lock] = {}
        self._init_client()

    def _python_lock(self, container_id: str) -> asyncio.Lock:
        """Serializes access to one container's interpreter.

        Sub-agents run in their parent's container and can run concurrently, so
        without this two calls write to the same interpreter socket at once and each
        reads back the other's answer.
        """
        lock = self._python_locks.get(container_id)
        if lock is None:
            lock = asyncio.Lock()
            self._python_locks[container_id] = lock
        return lock

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
        container_name = f"{SANDBOX_NAME_PREFIX}{conversation_id[:8]}"
        volumes_map = _build_volume_mounts(workspace_dir)

        def _create() -> str:
            try:
                existing = client.containers.get(container_name)
            except NotFound:
                existing = None

            if existing is not None:
                if has_forbidden_data_mount(existing):
                    # A container from before the /data mount was removed keeps
                    # leaking until replaced; its workspace lives on the host,
                    # so recreating loses nothing the conversation relies on.
                    try:
                        existing.stop(timeout=2)
                        existing.remove(v=True, force=True)
                    except Exception:
                        pass
                else:
                    if existing.status != "running":
                        existing.start()
                    return existing.id

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
        client = self._client

        def _start() -> bool:
            try:
                container = client.containers.get(container_id)
            except NotFound:
                return False
            if has_forbidden_data_mount(container):
                # Not revivable: this container predates the removal of the
                # /data mount and would keep exposing other conversations'
                # files. Reporting False makes the caller create a clean one.
                try:
                    container.stop(timeout=2)
                    container.remove(v=True, force=True)
                except Exception:
                    pass
                return False
            if container.status != "running":
                container.start()
            return True

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

    def _start_marked_exec(self, container_id: str, command: str, task_id: str) -> str:
        """Creates an exec for a long-running command, tagged so it can be killed later."""
        client = self._require_client()
        self._get_running_container_sync(container_id)
        return client.api.exec_create(
            container_id,
            cmd=["/bin/bash", "-c", command],
            stdout=True,
            stderr=True,
            workdir="/workspace",
            environment=[f"{BACKGROUND_TASK_MARKER}={task_id}"],
        )["Id"]

    async def start_background_command(
        self, container_id: str, command: str, task_id: str
    ) -> str:
        """Starts a long-running command in the container.

        Returns:
            str: Exec id, for streaming its output and collecting its exit code.
        """
        return await self._run_blocking(
            lambda: self._start_marked_exec(container_id, command, task_id)
        )

    async def stream_exec_output(self, exec_id: str) -> AsyncIterator[ExecChunk]:
        """Yields a running exec's output as it arrives, ending when the process exits.

        Output is streamed rather than redirected to a file inside the container. The
        previous approach detached the process into a fixed log path, so two tasks
        overwrote each other's log, nothing ever read it back, and the task stayed
        "running" forever because no exit code was ever collected.

        The docker SDK's stream is a blocking iterator, so it is drained on a worker
        thread and handed over through a queue.

        Yields:
            ExecChunk: A piece of stdout or stderr, in arrival order.
        """
        client = self._require_client()
        chunks: queue.Queue[Optional[ExecChunk]] = queue.Queue(maxsize=256)

        def _drain() -> None:
            try:
                stream: Iterator[Tuple[Optional[bytes], Optional[bytes]]] = (
                    client.api.exec_start(exec_id, stream=True, demux=True)
                )
                for stdout_bytes, stderr_bytes in stream:
                    if stdout_bytes:
                        chunks.put(
                            ExecChunk(stdout_bytes.decode("utf-8", "replace"), False)
                        )
                    if stderr_bytes:
                        chunks.put(
                            ExecChunk(stderr_bytes.decode("utf-8", "replace"), True)
                        )
            except Exception as error:  # surfaced as task stderr rather than lost
                chunks.put(ExecChunk(f"[stream ended: {error}]\n", True))
            finally:
                chunks.put(None)

        worker = threading.Thread(target=_drain, daemon=True)
        worker.start()

        loop = asyncio.get_running_loop()
        while True:
            chunk = await loop.run_in_executor(None, chunks.get)
            if chunk is None:
                return
            yield chunk

    async def exec_exit_code(self, exec_id: str) -> Optional[int]:
        """Reports how an exec finished, or None if it is still running."""
        client = self._require_client()
        try:
            inspection = await self._run_blocking(
                lambda: client.api.exec_inspect(exec_id)
            )
        except Exception:
            return None
        exit_code = inspection.get("ExitCode")
        return int(exit_code) if exit_code is not None else None

    async def kill_background_command(self, container_id: str, task_id: str) -> bool:
        """Kills the process started for a task, matching on its marker variable.

        Returns:
            bool: True if a process matched and was signalled.
        """

        def _kill() -> bool:
            container = self._get_running_container_sync(container_id)
            # Reading each process's own environ is what makes this exact: matching on
            # the command line would also kill an unrelated process that merely
            # mentions the same words.
            script = (
                "killed=0; for pid in /proc/[0-9]*; do "
                f'if tr "\\0" "\\n" < "$pid/environ" 2>/dev/null | grep -qx '
                f'"{BACKGROUND_TASK_MARKER}={task_id}"; then '
                'kill -9 "${pid##*/}" 2>/dev/null && killed=1; fi; done; '
                "test $killed -eq 1"
            )
            result = container.exec_run(cmd=["/bin/sh", "-c", script])
            return result.exit_code == 0

        try:
            return await self._run_blocking(_kill)
        except Exception:
            return False

    async def open_shell(self, container_id: str) -> SandboxShell:
        """Opens an interactive bash session with a real PTY inside the container.

        Returns:
            SandboxShell: Blocking handle for the shell's socket.
        """
        client = self._require_client()

        def _open() -> SandboxShell:
            container = self._get_running_container_sync(container_id)
            # The slim base images ship a colorless bash: no prompt colors, no
            # `ls` colors. A tiny rcfile turns both on for the user's terminal.
            container.exec_run(
                cmd=[
                    "/bin/sh",
                    "-c",
                    f"printf %s {_shell_quote(_SHELL_RCFILE)} > /tmp/.kayak_bashrc",
                ]
            )
            exec_id = client.api.exec_create(
                container_id,
                cmd=["/bin/bash", "--rcfile", "/tmp/.kayak_bashrc", "-i"],
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

    def _discard_python_session(self, container_id: str) -> None:
        session = self._python_sessions.pop(container_id, None)
        if session:
            session.close()

    def _open_python_session_sync(self, container_id: str) -> PythonSession:
        """Starts the REPL driver in the container and connects to it."""
        client = self._require_client()
        container = self._get_running_container_sync(container_id)

        # The driver is written into the container rather than mounted, so it
        # works no matter which image the sandbox uses.
        container.exec_run(
            cmd=[
                "python3",
                "-c",
                f"open('/tmp/.kayak_repl.py', 'w').write({PYTHON_REPL_DRIVER!r})",
            ]
        )

        exec_id = client.api.exec_create(
            container_id,
            cmd=["python3", "-u", "/tmp/.kayak_repl.py"],
            stdin=True,
            stdout=True,
            stderr=True,
            tty=False,
            workdir="/workspace",
        )["Id"]
        raw_socket = client.api.exec_start(exec_id, socket=True, tty=False)
        return PythonSession(raw_socket)

    async def run_python_code(
        self, container_id: str, code: str, timeout: int = 60
    ) -> str:
        """Runs Python in the container's persistent interpreter session.

        Variables, imports, and loaded data survive between calls -- the point
        is that expensive setup is paid once, not on every step. A timeout
        kills the session (the interpreter is still busy and cannot be reused),
        and the next call transparently starts a fresh one.
        """
        async with self._python_lock(container_id):
            session = await self._ensure_python_session(container_id)
            try:
                return await self._run_blocking(lambda: session.execute(code, timeout))
            except TimeoutError:
                self._discard_python_session(container_id)
                return (
                    f"Error: Execution timed out after {timeout} seconds and the Python"
                    " session was restarted; its variables are lost. Re-run your setup,"
                    " and put long-running work in a script via start_background_task."
                )
            except ConnectionError as error:
                self._discard_python_session(container_id)
                return (
                    f"Error: The Python session ended unexpectedly ({error}). A fresh"
                    " session will start on your next call; its variables are lost."
                )

    async def _ensure_python_session(self, container_id: str) -> PythonSession:
        """Returns the container's live interpreter, starting one if needed."""
        session = self._python_sessions.get(container_id)
        if session is not None and not session.dead:
            return session

        self._discard_python_session(container_id)
        session = await self._run_blocking(
            lambda: self._open_python_session_sync(container_id)
        )
        self._python_sessions[container_id] = session
        return session

    async def stop_and_remove_sandbox(self, container_id: str) -> None:
        """Destroys the container. Used when its conversation is deleted."""
        self._discard_python_session(container_id)
        self._python_locks.pop(container_id, None)
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

    async def stop_sandbox(self, container_id: str) -> None:
        """Stops the container without destroying it, keeping its filesystem."""
        self._discard_python_session(container_id)
        if not self._docker_available or not self._client:
            return

        client = self._client

        def _stop() -> None:
            try:
                client.containers.get(container_id).stop(timeout=2)
            except Exception:
                pass

        await self._run_blocking(_stop)

    async def shutdown_all(self) -> None:
        """Stops every sandbox, without destroying any of them.

        A sandbox is where an agent installed its packages and built its tooling.
        Removing containers at shutdown threw all of that away on every restart, while
        the rest of the code is written to revive a conversation's recorded container
        rather than replace it. Stopping leaves nothing running and loses nothing:
        opening the conversation again starts it back up.

        Every sandbox is stopped, not only the ones this process started. After a
        restart the owned set is empty while the previous run's containers are still
        running, so scoping this to them left orphans accumulating with each restart
        until someone noticed and pruned them by hand.
        """
        for container_id in list(self._owned_containers):
            await self.stop_sandbox(container_id)

        if not self._docker_available or not self._client:
            return
        client = self._client

        def _stop_strays() -> None:
            try:
                containers = client.containers.list(
                    filters={"name": SANDBOX_NAME_PREFIX, "status": "running"}
                )
            except Exception:
                return
            for container in containers:
                try:
                    container.stop(timeout=2)
                except Exception:
                    # One container refusing to stop must not strand the rest,
                    # and shutdown is already underway.
                    continue

        await self._run_blocking(_stop_strays)


sandbox_manager = SandboxManager()
