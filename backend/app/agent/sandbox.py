"""Docker sandbox lifecycle and command execution.

The docker SDK is entirely synchronous. Every call here is therefore dispatched to a
worker thread: running one inline would block the event loop for the duration of the
container operation, freezing SSE streams and every other conversation on the server
while a single agent waits on a build or a test run.
"""

import asyncio
from dataclasses import dataclass
from functools import partial
import io
import json
import logging
from pathlib import Path
import queue
import tarfile
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
import uuid
import docker
from docker.errors import ImageNotFound, NotFound
from backend.app.agent.python_repl import (
    PYTHON_REPL_DRIVER,
    demux_docker_frames,
    format_execution_result,
    scan_for_response,
)
from backend.app import docker_host
from backend.app.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: Exit status of a process killed with SIGKILL: by `timeout --signal=KILL` when it
#: runs out of time, or by the kernel when the container runs out of memory.
_KILLED_EXIT_CODE = 137

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

#: Share of Docker Desktop's memory a sandbox may use unless the user chose
#: otherwise. Not all of it: a sandbox runs per conversation, often next to a
#: model server, and one runaway process must not take the rest down with it.
RECOMMENDED_MEMORY_SHARE = 0.5

#: CPU quotas are expressed per period; Docker's default period is 100ms.
_CPU_PERIOD = 100_000

_GIB = 1024 ** 3


@dataclass(frozen=True)
class Allocation:
    """How much memory and CPU one sandbox may use."""
    memory_gb: float
    cpus: float


def recommended_allocation(
    total_memory_bytes: Optional[int], total_cpus: Optional[int]
) -> Optional[Allocation]:
    """What a sandbox gets by default: a share of Docker's memory and every core.

    Computed from the machine rather than fixed, so a Mac given more to Docker
    gives its agents more. None when Docker will not say what it has.
    """
    if not total_memory_bytes or not total_cpus:
        return None
    memory_gb = max(1, int(total_memory_bytes / _GIB * RECOMMENDED_MEMORY_SHARE))
    return Allocation(memory_gb=memory_gb, cpus=total_cpus)


def resolve_allocation(
    memory_gb: Optional[float],
    cpus: Optional[float],
    total_memory_bytes: Optional[int],
    total_cpus: Optional[int],
) -> Optional[Allocation]:
    """The user's choice where they made one, the recommendation otherwise.

    A choice larger than Docker's VM -- made before its memory was lowered in
    Docker Desktop, say -- is clamped to what the VM has rather than refused.
    """
    recommended = recommended_allocation(total_memory_bytes, total_cpus)
    if recommended is None:
        return None
    total_gb = total_memory_bytes / _GIB
    return Allocation(
        memory_gb=min(memory_gb, total_gb) if memory_gb else recommended.memory_gb,
        cpus=min(cpus, total_cpus) if cpus else recommended.cpus,
    )


def _limits(allocation: Allocation) -> Dict[str, Any]:
    """Docker's run/update arguments for an allocation."""
    memory = int(allocation.memory_gb * _GIB)
    return {
        "mem_limit": memory,
        # Docker refuses a memory limit above the swap limit, so the two move
        # together; twice the memory is what Docker itself defaults to.
        "memswap_limit": memory * 2,
        "cpu_period": _CPU_PERIOD,
        "cpu_quota": int(allocation.cpus * _CPU_PERIOD),
    }


def _shell_quote(text: str) -> str:
    """Single-quotes a string for safe embedding in a POSIX shell command."""
    return "'" + text.replace("'", "'\\''") + "'"


def _raw_socket(hijacked: Any) -> Any:
    """The socket behind a hijacked `docker exec`; docker-py wraps it in a SocketIO."""
    return hijacked._sock if hasattr(hijacked, "_sock") else hijacked


def _close_socket(sock: Any) -> None:
    """Tears a socket down, which also unblocks any thread parked reading it."""
    try:
        sock.shutdown(2)  # SHUT_RDWR
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


def _killed_message(exit_code: int, started: float, timeout: int) -> Optional[str]:
    """Explains a SIGKILL: a timeout if the time ran out, otherwise memory."""
    if exit_code != _KILLED_EXIT_CODE:
        return None
    if time.monotonic() - started >= timeout:
        return (
            f"Error: Timed out after {timeout} seconds and was killed. Use"
            " start_background_task for long-running work."
        )
    return (
        "Error: The process was killed, most likely for running out of memory in the"
        " sandbox."
    )


class ExecChunk:
    """One piece of output from a streaming exec."""

    __slots__ = ("text", "is_stderr")

    def __init__(self, text: str, is_stderr: bool) -> None:
        self.text = text
        self.is_stderr = is_stderr


def _build_volume_mounts(workspace_dir: Path) -> Dict[str, Dict[str, str]]:
    """The sandbox's volume mounts: the conversation's workspace, at `/workspace`.

    Only the conversation's own workspace is mounted -- nothing else, so an agent
    cannot read other conversations' files, the database, or settings.json with
    its plaintext provider API keys.
    """
    return {
        docker_host.host_path(workspace_dir): {
            "bind": "/workspace",
            "mode": "rw",
        },
    }


def _build_custom_tool_runner_script(
    entrypoint: str, tool_code: str, arguments: Dict[str, Any]
) -> str:
    """Builds a self-contained script that calls a custom tool's entrypoint once."""
    return f"""
{tool_code}


import asyncio as _kayak_asyncio
import inspect as _kayak_inspect
import json as _kayak_json
import sys as _kayak_sys

try:
    _kayak_result = {entrypoint}(**_kayak_json.loads({json.dumps(arguments)!r}))
    if _kayak_inspect.iscoroutine(_kayak_result):
        _kayak_result = _kayak_asyncio.run(_kayak_result)
    if _kayak_result is not None:
        print(_kayak_result)
except Exception as _kayak_error:
    print(f"Error: {{_kayak_error}}")
    _kayak_sys.exit(1)
"""


def _tar(files: Dict[str, str], directory: str) -> bytes:
    """Packs text files into a tar archive under one directory, for put_archive."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        folder = tarfile.TarInfo(directory)
        folder.type = tarfile.DIRTYPE
        folder.mode = 0o755
        archive.addfile(folder)
        for name, text in files.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(f"{directory}/{name}")
            info.size = len(data)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


@dataclass
class IsolatedRun:
    """How a command in a throwaway container ended."""
    #: None when it ran out of time and was killed.
    exit_code: Optional[int]
    stdout: str
    stderr: str


class SandboxShell:
    """An interactive TTY shell running inside a sandbox container.

    Wraps the raw hijacked socket from a `docker exec`. Every method blocks and
    must be called from a worker thread, never the event loop.
    """

    def __init__(self, client: docker.DockerClient, exec_id: str, raw_socket: Any):
        self._client = client
        self._exec_id = exec_id
        self._sock = _raw_socket(raw_socket)

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
        _close_socket(self._sock)


class PythonSession:
    """A persistent Python interpreter running inside a sandbox container.

    Wraps the stdin/stdout socket of the REPL driver exec. Everything here
    blocks and must run on a worker thread.
    """

    def __init__(self, raw_socket: Any):
        self._sock = _raw_socket(raw_socket)
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
        _close_socket(self._sock)
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
        self._client = docker_host.connect()
        self._docker_available = self._client is not None

    async def _run_blocking(self, func: Callable[[], T]) -> T:
        """Runs a blocking docker SDK call on the default thread pool."""
        return await asyncio.get_running_loop().run_in_executor(None, func)

    def _require_client(self) -> docker.DockerClient:
        """Returns the Docker client, reconnecting if Docker was down until now.

        Docker Desktop is often started after Kayak; a client probed once at import
        would report it unavailable until Kayak itself was restarted.
        """
        if not self._docker_available or not self._client:
            self._init_client()
        if not self._client:
            raise RuntimeError("Docker is not running. Start Docker Desktop and try again.")
        return self._client

    def client(self) -> Optional[docker.DockerClient]:
        """The Docker client, or None while Docker is not running."""
        try:
            return self._require_client()
        except RuntimeError:
            return None

    def _ensure_image(self, client: docker.DockerClient) -> str:
        """Returns the sandbox image, pulling it first if this Mac does not have it."""
        image = settings.DOCKER_SANDBOX_IMAGE
        try:
            client.images.get(image)
        except ImageNotFound:
            logger.info("Pulling the sandbox image %s", image)
            client.images.pull(image)
        return image

    def _allocation(self, client: docker.DockerClient) -> Optional[Allocation]:
        """The allocation new and revived sandboxes get, as of right now."""
        total_memory, total_cpus = docker_host.vm_resources(client)
        return resolve_allocation(
            settings.SANDBOX_MEMORY_GB, settings.SANDBOX_CPUS, total_memory, total_cpus
        )

    def _apply_allocation(self, client: docker.DockerClient, container: Any) -> None:
        """Brings an existing sandbox's limits in line with the current setting.

        Docker changes a container's limits in place, running or not, so a change
        made in Settings reaches every conversation the next time it runs rather
        than only conversations started afterwards.
        """
        allocation = self._allocation(client)
        if allocation is None:
            return
        wanted = _limits(allocation)
        host = container.attrs.get("HostConfig", {})
        if host.get("Memory") == wanted["mem_limit"] and host.get("CpuQuota") == wanted["cpu_quota"]:
            return
        try:
            container.update(**wanted)
        except Exception as error:
            logger.warning("Could not apply the sandbox allocation to %s: %s", container.name, error)

    async def prepare(self) -> None:
        """Pulls the sandbox image ahead of the first conversation, if it is missing."""
        try:
            await self._run_blocking(lambda: self._ensure_image(self._require_client()))
        except Exception as error:
            logger.warning("Could not prepare the sandbox image: %s", error)

    def _get_running_container_sync(self, container_id: str) -> Any:
        """Retrieves a Docker container and ensures it is in the running state."""
        client = self._require_client()
        container = client.containers.get(container_id)
        if container.status != "running":
            container.start()
        return container

    @staticmethod
    def _wrap_with_timeout(cmd: List[str], timeout: int) -> List[str]:
        """Prefixes a command with `timeout` so a hung process dies inside the container.

        Cancelling the Python-side wait would only abandon the worker thread; the
        process in the container has to be killed where it runs.
        """
        return ["timeout", "--signal=KILL", str(int(timeout))] + cmd

    async def _exec(
        self, container_id: str, cmd: List[str], timeout: int
    ) -> Tuple[str, str, int, float]:
        """Runs a command to completion: (stdout, stderr, exit code, start time)."""

        def _run() -> Tuple[str, str, int, float]:
            container = self._get_running_container_sync(container_id)
            started = time.monotonic()
            result = container.exec_run(
                cmd=self._wrap_with_timeout(cmd, timeout),
                workdir="/workspace",
                demux=True,
            )
            stdout, stderr = result.output
            return (
                stdout.decode("utf-8", errors="replace") if stdout else "",
                stderr.decode("utf-8", errors="replace") if stderr else "",
                result.exit_code,
                started,
            )

        return await self._run_blocking(_run)

    async def create_sandbox(
        self, conversation_id: str, workspace_dir: Path
    ) -> str:
        """Creates and starts an isolated Docker container for a conversation, mounting the workspace.

        Args:
            conversation_id: Conversation identifier.
            workspace_dir: Directory holding the conversation's workspace.

        Returns:
            str: Container ID.
        """
        container_name = f"{SANDBOX_NAME_PREFIX}{conversation_id[:8]}"
        volumes_map = _build_volume_mounts(workspace_dir)

        def _create() -> str:
            client = self._require_client()
            try:
                existing = client.containers.get(container_name)
            except NotFound:
                existing = None

            if existing is not None:
                self._apply_allocation(client, existing)
                if existing.status != "running":
                    existing.start()
                return existing.id

            allocation = self._allocation(client)
            container = client.containers.run(
                image=self._ensure_image(client),
                name=container_name,
                command="tail -f /dev/null",  # Keep container alive
                detach=True,
                working_dir="/workspace",
                volumes=volumes_map,
                network_mode="bridge",
                remove=False,
                **(_limits(allocation) if allocation else {}),
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

        def _start() -> bool:
            client = self._require_client()
            try:
                container = client.containers.get(container_id)
            except NotFound:
                return False
            self._apply_allocation(client, container)
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

    async def exec_command(self, container_id: str, command: str, timeout: int) -> str:
        """Executes a shell command inside the container and returns its combined output."""
        stdout, stderr, exit_code, started = await self._exec(
            container_id, ["/bin/bash", "-c", command], timeout
        )
        killed = _killed_message(exit_code, started, timeout)
        if killed:
            return killed

        output = []
        if stdout:
            output.append(stdout)
        if stderr:
            output.append(f"STDERR:\n{stderr}")
        if exit_code != 0:
            output.append(f"\n[Exit code: {exit_code}]")

        return "\n".join(output) if output else "Command executed with no output."

    async def _exec_python(self, container_id: str, python_code: str, timeout: int) -> str:
        """Executes a Python script inside the container."""
        stdout, stderr, exit_code, started = await self._exec(
            container_id, ["python3", "-c", python_code], timeout
        )
        killed = _killed_message(exit_code, started, timeout)
        if killed:
            return killed
        if exit_code != 0 and stderr:
            return f"Error ({exit_code}):\n{stderr}"
        return stdout or stderr or "Execution completed with no output."

    async def exec_custom_tool(
        self,
        container_id: str,
        entrypoint: str,
        tool_code: str,
        arguments: Dict[str, Any],
        timeout: int = 60,
    ) -> str:
        """Calls a custom tool's entrypoint inside the conversation's container."""
        runner_script = _build_custom_tool_runner_script(entrypoint, tool_code, arguments)
        return await self._exec_python(container_id, runner_script, timeout)

    async def run_isolated(
        self, files: Dict[str, str], command: List[str], timeout: int
    ) -> IsolatedRun:
        """Runs a command in a fresh container from the sandbox image, then removes it.

        Used to test code before it is trusted anywhere: the files are copied in
        rather than mounted, and the command runs with them as its working directory.
        """

        def _run() -> IsolatedRun:
            client = self._require_client()
            container = client.containers.create(
                image=self._ensure_image(client),
                # Named like every sandbox, so a shutdown mid-run stops it too.
                name=f"{SANDBOX_NAME_PREFIX}isolated-{uuid.uuid4().hex[:8]}",
                command=command,
                working_dir="/isolated",
                detach=True,
            )
            try:
                container.put_archive("/", _tar(files, "isolated"))
                container.start()
                try:
                    exit_code: Optional[int] = container.wait(timeout=timeout).get("StatusCode")
                except Exception:
                    # The wait timed out; the code is still running.
                    container.kill()
                    exit_code = None
                return IsolatedRun(
                    exit_code=exit_code,
                    stdout=container.logs(stdout=True, stderr=False).decode("utf-8", "replace"),
                    stderr=container.logs(stdout=False, stderr=True).decode("utf-8", "replace"),
                )
            finally:
                container.remove(force=True)

        return await self._run_blocking(_run)

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

        They are stopped concurrently because the time this takes is a hard budget,
        not a preference: the whole shutdown has to finish inside the grace period the
        daemon allows before it kills the server outright, and that period is a local
        Docker setting that has been observed as low as three seconds. Stopping in
        sequence spent two of those seconds per sandbox, so a busy install ran out of
        time and left the rest running.
        """
        owned = list(self._owned_containers)
        for container_id in owned:
            self._discard_python_session(container_id)

        if not self._docker_available or not self._client:
            return
        client = self._client

        def _targets() -> list[str]:
            found = list(owned)
            try:
                found.extend(
                    container.id
                    for container in client.containers.list(
                        filters={"name": SANDBOX_NAME_PREFIX, "status": "running"}
                    )
                )
            except Exception:
                pass
            return list(dict.fromkeys(found))

        def _stop(container_id: str) -> None:
            try:
                # A shorter grace than elsewhere: the sandbox is being stopped, not
                # destroyed, so nothing is lost by killing one that ignores the
                # signal, and the whole shutdown is racing the daemon's own timer.
                client.containers.get(container_id).stop(timeout=1)
            except Exception:
                # One container refusing to stop must not strand the rest, and
                # shutdown is already underway.
                pass

        targets = await self._run_blocking(_targets)
        await asyncio.gather(
            *(self._run_blocking(partial(_stop, container_id)) for container_id in targets)
        )


sandbox_manager = SandboxManager()
