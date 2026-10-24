import asyncio
import collections
import logging
import os
import re
import time
import uuid
from pathlib import Path
import threading
from typing import Any, Dict, List, Optional, Set, Tuple
import docker
from docker.errors import APIError, DockerException, NotFound, ImageNotFound
import httpx
import requests
from backend.app import docker_host
from backend.app.config import settings
from backend.app.inference import failures, metal
from backend.app.inference.failures import short_name
from backend.app.inference.models import (
    HostCapability,
    Modality,
    DeployRequest,
    DeploymentProgress,
    ServerState,
)
from backend.app.inference.runtimes import Runtime, SpecContext

logger = logging.getLogger(__name__)

#: How often the manager re-checks reality (container state, endpoint health) on its
#: own. Without this, a server that crashed after coming up stayed "ONLINE" in every
#: open tab until something happened to request /status.
WATCHDOG_INTERVAL_SECONDS = 10.0

#: A start gives up once its server has printed nothing for this long. Downloading
#: tens of gigabytes of weights takes far longer than any fixed limit someone would
#: pick, but it is never silent: the download reports progress, and loading logs as it
#: goes. Silence is what a wedged start looks like.
START_IDLE_TIMEOUT_SECONDS = 600

#: The longest any start may take, however chatty. Generous on purpose -- a slow
#: connection and a large model are an ordinary combination -- and there only so that
#: nothing can stay "starting" forever.
START_TIMEOUT_CAP_SECONDS = 3 * 60 * 60

#: The longest a start on the Mac's GPU may take. The launcher reports no progress
#: while it downloads and loads, so there is no silence to measure and only a cap
#: can end a load that has wedged; three hours of "Loading…" was too long to show.
METAL_START_TIMEOUT_SECONDS = 60 * 60

#: How often a start measures its download on disk. The engine does not always print
#: progress while it downloads weights, so a large download on a slow connection
#: could go quiet for longer than the silence limit while working perfectly; the
#: files growing is a sign of life too.
DOWNLOAD_CHECK_INTERVAL_SECONDS = 30.0

CONTAINER_CHECK_INTERVAL_SECONDS = 5.0

#: How long the launcher may stop reporting during a GPU start before the start is
#: given up. It rewrites its status every couple of seconds, so this is several
#: missed beats -- the launcher has gone, not hiccuped.
LAUNCHER_SILENCE_SECONDS = 20.0

#: A ready server's health check. Generous, because a model busy answering on the CPU
#: can be slow to answer anything else, and it takes two minutes of failures in a row
#: (checks run every WATCHDOG_INTERVAL_SECONDS) to count: one slow answer used to drop
#: a working model back to "Starting", and half a minute of a busy Mac marked a
#: healthy model as stopped.
READY_CHECK_TIMEOUT_SECONDS = 5.0
READY_FAILURES_BEFORE_ERROR = 12

#: Probe timeout for a server that is not ready yet, where a slow answer costs nothing.
STARTING_CHECK_TIMEOUT_SECONDS = 1.5

#: Grace given to a server container when Kayak itself is shutting down. Short,
#: because every modality's container has to be stopped inside the period the
#: daemon allows the backend before it kills it -- and a server that ignores the
#: signal loses nothing by being killed, since its weights are on disk.
SHUTDOWN_STOP_TIMEOUT_SECONDS = 3

_LOADING_STATES = frozenset({
    ServerState.PULLING_IMAGE,
    ServerState.STARTING_CONTAINER,
    ServerState.LOADING,
})

_ACTIVE_STATES = _LOADING_STATES | {ServerState.READY}

#: Docker failures that may mean the daemon itself went away, rather than that it
#: refused something. Only these are worth asking Docker whether it is still there.
_CONNECTION_FAILURES = (DockerException, requests.RequestException, ConnectionError)

#: Lines like "ValueError: Available memory on node 0 ... is less than requested".
_ERROR_LINE = re.compile(r"\b([A-Za-z_]*(?:Error|Exception)):\s*(\S.*)$")

#: Log noise that names an exception type but never explains a failure. "WARNING"
#: covers lines vLLM logged at warning level and recovered from -- e.g. "Failed to
#: create oneDNN linear, fallback to torch linear. Exception: ..." -- which used to
#: be reported as the crash reason while the fatal error sat further down.
_UNHELPFUL_ERROR_MARKERS = (
    "resource_tracker",
    "FutureWarning",
    "DeprecationWarning",
    "UserWarning",
    "WARNING",
)

#: The engine image is several gigabytes, and saying so up front is what makes the
#: wait read as a download rather than a hang.
ENGINE_DOWNLOAD_MESSAGE = "Downloading the software that runs models (one time, several GB)…"

#: Hugging Face's progress bars, as tqdm draws them:
#: "model-00001-of-00004.safetensors:  45%|████▌     | 2.23G/4.97G [00:30<00:37, 73.4MB/s]".
_PROGRESS_BAR = re.compile(r"(\d{1,3})%\|")
_PROGRESS_BYTES = re.compile(
    r"^(?P<label>.*?)\s*(?P<pct>\d{1,3})%\|[^|]*\|\s*"
    r"(?P<done>[\d.]+)(?P<done_unit>[kMGTP]?)B?/(?P<total>[\d.]+)(?P<total_unit>[kMGTP]?)B?"
)
_UNIT_BYTES = {"": 1, "k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15}

#: Lines that say the download is over and the weights are being read into memory.
_LOADING_WEIGHTS = re.compile(
    r"Starting to load model|Loading safetensors checkpoint shards|Loading weights|"
    r"Model loading took|\bLoading \S+ with the \S+ backend",
    re.IGNORECASE,
)


def extract_failure_reason(log_lines: List[str]) -> Optional[str]:
    """Finds the line in a container's output that explains why it died.

    Used for the server log and the model's Details, never for the headline: that is
    :func:`failures.explain`'s job. Quoting the last few lines instead is unreliable: a
    crashing vLLM prints shutdown tracebacks and interpreter warnings after the real
    error, so the tail is usually noise while the sentence naming the cause sits
    further up.

    Args:
        log_lines: Captured container output, oldest first.

    Returns:
        Optional[str]: The most specific error message found, or None.
    """
    best: Optional[str] = None

    for line in log_lines:
        match = _ERROR_LINE.search(line)
        if not match:
            continue
        message = match.group(2).strip()
        if not message or any(marker in line for marker in _UNHELPFUL_ERROR_MARKERS):
            continue
        # Later errors are usually wrappers around the first real one, so the earliest
        # informative message is kept.
        if best is None:
            best = f"{match.group(1)}: {message}"

    return best


class DownloadProgress:
    """How far a model download has got, from Hugging Face's per-file progress bars.

    A model is several files downloaded side by side, each with its own bar, so the
    percentage of whichever bar printed last jumps around between files. Summing the
    bytes every bar reports gives one figure that moves the way a person expects.
    """

    def __init__(self) -> None:
        self._files: Dict[str, Tuple[float, float]] = {}
        self._shown = 0

    def update(self, line: str) -> Optional[int]:
        """Reads one log line, returning the overall percentage if it was a bar.

        Never goes backwards: a file whose bar has not appeared yet is not counted,
        so the true figure dips each time one starts, and a number that falls reads
        as something going wrong.
        """
        match = _PROGRESS_BYTES.search(line)
        if not match:
            return None
        label = match.group("label").strip()
        # The "Fetching 12 files" bar counts files, not bytes.
        if "fetching" in label.lower():
            return None
        done = float(match.group("done")) * _UNIT_BYTES[match.group("done_unit")]
        total = float(match.group("total")) * _UNIT_BYTES[match.group("total_unit")]
        if total <= 0:
            return None
        self._files[label] = (min(done, total), total)
        downloaded = sum(part for part, _ in self._files.values())
        size = sum(whole for _, whole in self._files.values())
        self._shown = max(self._shown, min(100, int(downloaded * 100 / size)))
        return self._shown


class _EventQueue(asyncio.Queue):
    """One subscriber's events, which never loses the latest status.

    Status and log events share one ordered queue, so a log line written after a
    status change still arrives after it. But a burst of log lines -- a download
    prints hundreds -- used to push the final "ready" out of a full queue, and that
    tab then showed a start in progress forever. So a status replaces any earlier
    status for the same server still waiting in the queue, and when the queue is full
    it is a log line that makes room, never a status.
    """

    def _init(self, maxsize: int) -> None:
        self._queue: collections.deque = collections.deque()

    def _put(self, item: Dict[str, Any]) -> None:
        if item.get("type") == "status":
            for queued in list(self._queue):
                if queued.get("type") == "status" and queued.get("modality") == item.get("modality"):
                    self._queue.remove(queued)
        self._queue.append(item)

    def _get(self) -> Dict[str, Any]:
        return self._queue.popleft()

    def put_nowait(self, item: Dict[str, Any]) -> None:
        if self.full():
            oldest_log = next(
                (queued for queued in self._queue if queued.get("type") != "status"),
                None,
            )
            if oldest_log is not None:
                self._queue.remove(oldest_log)
            elif item.get("type") != "status":
                # Nothing but statuses waiting: the newest log line is the one to lose.
                return
            else:
                self._queue.popleft()
        super().put_nowait(item)


class StatusBroadcaster:
    """Fans status and log events out to every subscribed client.

    Shared by all managers so that one connection carries every modality: browsers
    cap concurrent HTTP/1.1 connections at six per origin, and opening a stream per
    server would spend that budget on idle servers. Each event names its modality, so
    a client can tell a voice model's startup from a text model's.
    """

    def __init__(self) -> None:
        self._listeners: Set[asyncio.Queue] = set()

    def subscribe(self, greeting: List[Dict[str, Any]]) -> asyncio.Queue:
        """Subscribes to live events, opening with the current state of every server.

        The greeting must use the same envelope as every later broadcast. It used to
        push the bare status dict, which the client could not read -- so a tab that
        reconnected after an app restart received nothing and kept showing whatever it
        believed before the restart.
        """
        queue: asyncio.Queue = _EventQueue(maxsize=100)
        self._listeners.add(queue)
        for event in greeting:
            queue.put_nowait(event)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._listeners.discard(queue)

    def publish(self, data: Dict[str, Any]) -> None:
        """Dispatches an event to all active SSE queues.

        A full queue drops its oldest entry, not the new one: the latest status is
        the only one that matters, and a slow consumer that silently lost the final
        "ready" would keep showing a deployment in progress forever.
        """
        for queue in list(self._listeners):
            try:
                queue.put_nowait(data)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait(data)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass


class ServerManager:
    """Manages the lifecycle of one Docker container serving one model.

    The manager knows how to pull, start, watch, reconcile and stop a server; it does
    not know what kind of server it is. That comes from the :class:`Runtime` it is
    constructed with, so a new modality is a new runtime rather than a change here.
    """

    def __init__(
        self,
        runtime: Runtime,
        broadcaster: Optional[StatusBroadcaster] = None,
    ):
        self._runtime = runtime
        self._broadcaster = broadcaster or StatusBroadcaster()
        self._init_state()
        self._init_docker()

    @property
    def runtime(self) -> Runtime:
        return self._runtime

    @property
    def modality(self) -> Modality:
        return self._runtime.modality

    @property
    def container_name(self) -> str:
        return self._runtime.container_name

    def _init_state(self) -> None:
        """Initializes in-memory state, shared with tests that skip Docker probing."""
        self._client: Optional[docker.DockerClient] = None
        self._docker_available: bool = False
        self._status: DeploymentProgress = self._idle_status()
        self._log_history: List[str] = []
        self._monitor_task: Optional[asyncio.Task] = None
        self._metal_monitor_task: Optional[asyncio.Task] = None
        self._watchdog_task: Optional[asyncio.Task] = None
        #: Strong reference to an automatic retry, so the task is not garbage
        #: collected mid-flight.
        self._retry_task: Optional[asyncio.Task] = None
        self._log_stop_event: Optional[threading.Event] = None
        # Serializes deploy/stop/sync. Without it, two rapid "start" clicks raced two
        # deployment runners that force-removed each other's containers.
        self._control_lock = asyncio.Lock()
        # Incremented on every deploy or stop. A background monitor captures the value
        # it was started under and stops touching shared state the moment it is
        # superseded, so a cancelled or replaced deployment can never overwrite the
        # status of the one that followed it.
        self._deploy_generation = 0
        #: The request behind the current deployment, for telling "same model again"
        #: from "same model with different settings".
        self._active_request: Optional[DeployRequest] = None
        #: When the server last printed anything. A start is given up after a long
        #: silence rather than after a fixed time, because a big download is slow
        #: but never quiet.
        self._last_activity: float = time.monotonic()
        #: Consecutive failed health checks of a ready server.
        self._ready_failures = 0
        #: The last percentage logged per progress bar, so a bar redrawn ten times a
        #: second does not fill the log with copies of itself.
        self._progress_logged: Dict[str, int] = {}
        self._download = DownloadProgress()
        #: Set once the weights are being loaded, after which download bars -- a
        #: late small file -- must not take the status back to "Downloading".
        self._loading_weights = False

    def _idle_status(self) -> DeploymentProgress:
        """A status describing this runtime's server before anything has started."""
        port = self._runtime.default_port
        return DeploymentProgress(
            modality=self._runtime.modality,
            message="Not running.",
            port=port,
            endpoint=self._runtime.api_base(port),
        )

    def _init_docker(self) -> None:
        self._client = docker_host.connect()
        self._docker_available = self._client is not None

    async def _ensure_docker(self) -> bool:
        """Re-probes the Docker daemon whenever it was last seen unavailable.

        The daemon used to be probed once, at process start. Anyone who launched
        Kayak before Docker Desktop -- the normal order for someone opening their
        laptop -- was told "Docker is not available" until they restarted Kayak.
        """
        if self._docker_available and self._client:
            return True
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._init_docker)
        return self._docker_available

    async def _docker_alive(self) -> bool:
        """Whether the Docker daemon answers right now.

        Asked after a failure that could be Docker itself going away, so that
        "Docker Desktop was quit" is reported as that rather than as whatever the
        broken connection made the failing call raise.
        """
        client = self._client
        if client is None:
            return False

        def _ping() -> bool:
            try:
                client.ping()
                return True
            except Exception:
                return False

        loop = asyncio.get_running_loop()
        alive = await loop.run_in_executor(None, _ping)
        if not alive:
            # Re-probed from scratch next time, so Docker coming back is noticed.
            self._docker_available = False
        return alive

    def _superseded(self, generation: int) -> bool:
        """True when a newer deploy or stop has taken over since this task began."""
        return generation != self._deploy_generation

    #: How many consecutive host ports to try when the configured one is taken.
    #: On a launcher install, Kayak itself is published on the default vLLM port,
    #: so without a fallback no container model could ever start there.
    PORT_FALLBACK_ATTEMPTS = 10

    #: Fragments of the Docker error raised when a host port cannot be bound.
    _PORT_TAKEN_MARKERS = ("port is already allocated", "address already in use")

    def _api_base_for_port(self, port: int) -> str:
        """The OpenAI-compatible base URL for a server published on `port`."""
        return self._runtime.api_base(port)

    def _health_url(self, port: Optional[int] = None) -> str:
        """The URL that proves the server is up, on the port it actually publishes.

        That port may differ from the configured default when the default was taken.
        """
        port = port or self._status.port or self._runtime.default_port
        path = self._runtime.health_path()
        # The api base already carries the /v1 prefix that the health path repeats.
        suffix = path[len("/v1"):] if path.startswith("/v1") else path
        return self._api_base_for_port(port).rstrip("/") + suffix

    def _container_host_port(self, container: Any) -> Optional[int]:
        """The host port a discovered container actually publishes, if readable."""
        try:
            ports = container.attrs["NetworkSettings"]["Ports"][
                f"{self._runtime.container_port}/tcp"
            ]
            return int(ports[0]["HostPort"])
        except (KeyError, IndexError, TypeError, ValueError):
            return None

    def _ensure_log_streamer(self, container: Any) -> None:
        """Spawns a log streaming thread if one isn't already running."""
        if self._log_stop_event:
            return
        try:
            loop = asyncio.get_running_loop()
            self._log_stop_event = threading.Event()
            self._spawn_log_stream_thread(container, loop, self._log_stop_event)
        except Exception:
            pass

    def status_event(self) -> Dict[str, Any]:
        """This server's current status, in the SSE envelope clients expect.

        Stamped like every broadcast frame, so a client never has to handle two
        shapes: the greeting a reconnecting tab receives reads exactly like the
        updates that follow it.
        """
        return {
            "modality": self._runtime.modality.value,
            "type": "status",
            "data": self.get_status().model_dump(),
        }

    def _broadcast(self, data: Dict[str, Any]) -> None:
        """Publishes an event, stamped with the modality it describes.

        Without the stamp a shared stream would be unreadable: a client could not
        tell which server a log line or a state change belongs to.
        """
        self._broadcaster.publish({"modality": self._runtime.modality.value, **data})

    def _broadcast_status(self) -> None:
        self._broadcast({"type": "status", "data": self._status.model_dump()})

    def _add_log(self, line: str) -> None:
        """Appends a line to the logs buffer and broadcasts to subscribers.

        Every line also counts as a sign of life for the start deadline, and a
        download's progress bars are read on the way past to keep the status'
        percentage current.
        """
        clean_line = line.strip()
        if not clean_line:
            return
        self._last_activity = time.monotonic()

        bar = _PROGRESS_BAR.search(clean_line)
        if bar:
            label = clean_line[: bar.start()].strip()
            percent = int(bar.group(1))
            self._note_download(clean_line)
            if self._progress_logged.get(label) == percent:
                return
            self._progress_logged[label] = percent
        elif _LOADING_WEIGHTS.search(clean_line):
            self._note_loading_weights()

        self._log_history.append(clean_line)
        if len(self._log_history) > 300:
            self._log_history = self._log_history[-300:]

        self._status.logs_tail = self._log_history[-30:]
        self._broadcast({"type": "log", "line": clean_line})

    def _note_download(self, line: str) -> None:
        """Turns a progress bar into "Downloading <model>… 45%" on the status."""
        percent = self._download.update(line)
        if (
            percent is None
            or self._loading_weights
            or self._status.state != ServerState.LOADING
            or not self._status.model_id
        ):
            return
        message = f"Downloading {short_name(self._status.model_id)}… {percent}%"
        if message != self._status.message:
            self._update_status(state=ServerState.LOADING, message=message)

    def _note_loading_weights(self) -> None:
        """Marks the move from downloading to loading the weights into memory."""
        if self._status.state != ServerState.LOADING or not self._status.model_id:
            return
        self._loading_weights = True
        message = f"Loading {short_name(self._status.model_id)}…"
        if message != self._status.message:
            self._update_status(state=ServerState.LOADING, message=message)

    def _update_status(
        self,
        state: ServerState,
        message: str,
        error: Optional[str] = None,
        exit_code: Optional[int] = None,
        crashed: bool = False,
    ) -> None:
        """Updates internal telemetry and broadcasts to frontend.

        An error, an exit code and the "crashed" mark only describe an ERROR, so
        any other state clears them: a model restarted after a failure must not
        carry the old failure's explanation into its "Starting" row.
        """
        self._status.state = state
        self._status.message = message
        if state == ServerState.ERROR:
            if error is not None:
                self._status.error = error
            if exit_code is not None:
                self._status.exit_code = exit_code
            self._status.crashed = crashed
        else:
            self._status.error = None
            self._status.exit_code = None
            self._status.crashed = False

        self._broadcast_status()

    def get_status(self) -> DeploymentProgress:
        """Returns the current deployment and server status."""
        self._status.logs_tail = self._log_history[-30:]
        return self._status

    def _has_token(self) -> bool:
        return bool(settings.HUGGINGFACE_API_KEY)

    def _memory_limited(self) -> bool:
        return bool(self._active_request and self._active_request.memory_limit_gb)

    def _explain(
        self,
        exit_code: Optional[int] = None,
        oom_killed: bool = False,
        *,
        gpu: bool = False,
        was_ready: bool = False,
        extra: Optional[List[str]] = None,
    ) -> str:
        """Explains a failure from this server's output, in words for a person."""
        return failures.explain(
            self._log_history + (extra or []),
            exit_code,
            oom_killed,
            gpu=gpu,
            memory_limited=self._memory_limited(),
            has_token=self._has_token(),
            model_id=self._status.model_id,
            offers_context="max_model_len" in self._runtime.tunable_fields,
            was_ready=was_ready,
        )

    def _begin(self, request: DeployRequest, message: str, port: int) -> None:
        """Replaces the status with a fresh start of `request`, and announces it."""
        self._log_history.clear()
        self._progress_logged = {}
        self._download = DownloadProgress()
        self._loading_weights = False
        self._ready_failures = 0
        self._last_activity = time.monotonic()
        self._status = DeploymentProgress(
            # Rebuilding the status must not silently relabel the server: without
            # this the field falls back to its default and a speech deployment
            # would broadcast itself as the text one.
            modality=self._runtime.modality,
            model_id=request.model_id,
            state=ServerState.STARTING_CONTAINER,
            message=message,
            port=port,
            endpoint=self._api_base_for_port(port),
        )
        self._active_request = request
        self._broadcast_status()

    def _fail_request(
        self, request: DeployRequest, error: str, message: Optional[str] = None
    ) -> DeploymentProgress:
        """Reports a start refused before anything was started.

        Carries the model id like any other failure, so the row, the toast and the
        chat composer can all say which model could not start.
        """
        self._status = DeploymentProgress(
            modality=self._runtime.modality,
            model_id=request.model_id,
            state=ServerState.ERROR,
            message=message or failures.couldnt_start(request.model_id),
            error=error,
            port=self._runtime.default_port,
            endpoint=self._api_base_for_port(self._runtime.default_port),
        )
        self._active_request = None
        self._broadcast_status()
        return self.get_status()

    def _release_metal(self) -> None:
        """Tells the launcher no GPU server is wanted, without letting a failure out.

        Called on every path that ends a GPU model -- stop, failure, timeout, quit --
        because a "running" left in desired.json is a model the launcher starts again
        on its own, at the next launch if not before. A write that fails is logged
        rather than raised: the stop or failure being reported has happened either
        way, and refusing to report it would leave the model stuck where it was.
        """
        if not self._runtime.supports_metal:
            return
        # No launcher is here and none has ever used this data folder -- Kayak run
        # from docker compose -- so there is no GPU server to stop, and no folder
        # to create.
        if not metal.control_dir().exists() and not metal.read_status().supported:
            return
        try:
            metal.write_desired(running=False)
        except OSError:
            logger.exception("Could not tell the launcher to stop the GPU server")

    async def deploy_model(self, request: DeployRequest) -> DeploymentProgress:
        """Deploys a Hugging Face model on local vLLM or Apple GPU Metal via launcher.

        Serialized with stop and status-sync: overlapping deploys and stops used to
        interleave freely, so two rapid clicks could race two deployment runners
        that removed each other's containers.

        Args:
            request: Configuration parameters for the server.

        Returns:
            Current deployment status.
        """
        async with self._control_lock:
            return await self._deploy_locked(request)

    async def _deploy_locked(self, request: DeployRequest) -> DeploymentProgress:
        name = short_name(request.model_id)

        # 1. MLX models run on the Apple GPU, served by the launcher rather than a
        # container. Runtimes that do not support it skip this branch entirely.
        if self._runtime.supports_metal and metal.is_mlx_model(request.model_id):
            metal_status = metal.read_status()
            if not metal_status.supported:
                await self._stop_locked(announce=False)
                return self._fail_request(request, metal_unavailable_message(metal_status))

            if (
                self._status.model_id == request.model_id
                and self._status.state in _ACTIVE_STATES
                and (
                    # Already coming up: repeated clicks must not restart it.
                    (self._metal_monitor_task and not self._metal_monitor_task.done())
                    # Already serving. "ready" alone is not enough: the launcher may
                    # still be reporting on a different model it served until now.
                    or (
                        metal_status.state == "ready"
                        and metal_status.model == request.model_id
                    )
                )
            ):
                return self.get_status()

            await self._stop_locked(announce=False)
            generation = self._deploy_generation

            # The token lets the monitor tell a status answering this request
            # from a stale one: the launcher reconciles every couple of
            # seconds, and until it does, the status file still describes the
            # previous server — often as "ready".
            request_token = uuid.uuid4().hex
            # Written before anything is announced. A start announced first and then
            # unable to write this file stayed "Starting" for good, with every Start
            # disabled and Stop failing on the same write.
            try:
                metal.write_desired(
                    running=True,
                    model=request.model_id,
                    request=request_token,
                    hf_token=settings.HUGGINGFACE_API_KEY or None,
                )
            except OSError:
                logger.exception("Could not ask the launcher to start %s", request.model_id)
                return self._fail_request(request, failures.SAVE_FAILED)

            # The launcher's port is only meaningful once it reports "ready": until
            # then it describes the server being replaced, or nothing at all.
            self._begin(request, f"Loading {name} on your Mac's GPU…", self._runtime.default_port)
            self._add_log(f"ℹ Asking for {request.model_id} to be loaded on the Mac's GPU.")

            self._metal_monitor_task = asyncio.create_task(
                self._run_metal_deployment(request.model_id, request_token, generation)
            )
            return self.get_status()

        # 2. Everything else runs in a container.
        if not await self._ensure_docker():
            # Whatever was running is stopped first, as any other start would, so
            # the error describes the only state there is.
            await self._stop_locked(announce=False)
            return self._fail_request(
                request,
                error="Open Docker Desktop, wait for it to finish starting, then try again.",
                message="Docker Desktop isn't running.",
            )

        # Guard: if already deploying or serving the same model, skip -- unless the
        # caller explicitly asked to restart, which is how "same model, different
        # settings" is applied. Bare deploys (from the chat composer) stay a no-op so
        # they can never restart a server someone tuned deliberately.
        if (
            self._status.model_id == request.model_id
            and self._status.state in _ACTIVE_STATES
            and not request.force_restart
        ):
            return self.get_status()

        # Stops the current container, and a GPU server if one was running or may
        # still be: a text model on the CPU replaces one on the GPU.
        await self._stop_locked(announce=False)
        generation = self._deploy_generation

        # Created before anything is announced, for the same reason as the GPU
        # request above: a start that cannot write its cache must fail, not hang.
        hf_cache_dir = settings.DATA_DIR / "huggingface_cache"
        try:
            hf_cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.exception("Could not create the model cache at %s", hf_cache_dir)
            return self._fail_request(request, failures.SAVE_FAILED)

        self._begin(request, f"Getting ready to start {name}…", self._runtime.default_port)

        self._monitor_task = asyncio.create_task(
            self._run_deployment(request, hf_cache_dir, generation)
        )
        return self.get_status()

    async def _run_metal_deployment(
        self,
        model_id: str,
        request_token: Optional[str] = None,
        generation: Optional[int] = None,
        timeout_seconds: int = METAL_START_TIMEOUT_SECONDS,
    ) -> None:
        """Monitors launcher status while starting a Metal server.

        Only statuses that answer *this* deployment are acted on. The launcher
        reconciles on a delay, so the first polls routinely read a file still
        describing the previous server — trusting its "ready" is exactly the
        bug where a model showed as serving the instant start was clicked.
        A launcher new enough to echo request tokens is matched on the token;
        an older one is matched on the model id, which still filters out
        statuses about a different model.

        The launcher streams no output, so there is no silence to time out on:
        only the overall cap applies, and a launcher that stops reporting at all
        ends the wait within seconds instead.
        """
        started = time.monotonic()
        if generation is None:
            generation = self._deploy_generation
        name = short_name(model_id)
        silent_since: Optional[float] = None

        def answers_this_deployment(status) -> bool:
            if request_token and status.acknowledges_requests:
                return status.request == request_token
            return status.model == model_id

        try:
            while time.monotonic() - started < timeout_seconds:
                await asyncio.sleep(1.0)
                metal_status = metal.read_status()

                # Bail if deployment was cancelled, stopped, or replaced
                if self._superseded(generation) or self._status.state in (
                    ServerState.STOPPED,
                    ServerState.IDLE,
                    ServerState.ERROR,
                ):
                    return

                # A missing or stale status file reads as "unsupported". Read as
                # that literally, a launcher that died mid-start left the model
                # "Starting" for the whole cap.
                if not metal_status.supported:
                    now = time.monotonic()
                    if silent_since is None:
                        silent_since = now
                    elif now - silent_since > LAUNCHER_SILENCE_SECONDS:
                        self._add_log("✗ The launcher stopped reporting while the model was starting.")
                        self._release_metal()
                        self._update_status(
                            state=ServerState.ERROR,
                            message=failures.couldnt_start(model_id),
                            error=failures.LAUNCHER_GONE,
                        )
                        return
                    continue
                silent_since = None

                if not answers_this_deployment(metal_status):
                    continue

                if metal_status.state == "ready":
                    self._status.port = metal_status.port or self._runtime.default_port
                    self._status.endpoint = self._api_base_for_port(self._status.port)
                    self._ready_failures = 0
                    self._update_status(
                        state=ServerState.READY,
                        message=f"{name} is ready.",
                    )
                    self._add_log(f"✓ {model_id} is loaded on the Mac's GPU.")
                    return
                elif metal_status.state == "installing":
                    msg = "Setting up GPU support (one time, a few minutes)…"
                    if self._status.message != msg:
                        self._update_status(state=ServerState.LOADING, message=msg)
                        self._add_log(f"ℹ {msg}")
                elif metal_status.state == "starting":
                    msg = f"Loading {name} on your Mac's GPU…"
                    if self._status.message != msg or self._status.state != ServerState.LOADING:
                        self._update_status(state=ServerState.LOADING, message=msg)
                        self._add_log(f"ℹ {msg}")
                elif metal_status.state == "error":
                    self._report_metal_error(model_id, metal_status, was_ready=False)
                    return

            if not self._superseded(generation):
                self._add_log("✗ The model did not finish starting in time.")
                self._release_metal()
                self._update_status(
                    state=ServerState.ERROR,
                    message=failures.couldnt_start(model_id),
                    error=failures.TIMED_OUT,
                )
        except asyncio.CancelledError:
            pass

    def _report_metal_error(self, model_id: str, metal_status: Any, was_ready: bool) -> None:
        """Moves a GPU model to ERROR with the launcher's reason, in plain words.

        Newer launchers report a sentence written for a person and keep the raw
        output in ``error_detail``; older ones put the raw output in ``error``.
        Either way the raw text goes to Details, and only a sentence reaches the
        headline. The desired state is reset too, or the launcher would try again
        on its own -- at the next launch, if not before.
        """
        raw = metal_status.error_detail or metal_status.error or ""
        if raw:
            logger.warning("GPU server for %s failed: %s", model_id, raw)
            for line in raw.splitlines()[-40:]:
                self._add_log(f"✗ {line}" if line.strip() else line)

        if metal_status.error and (
            metal_status.separates_error_detail or not failures.looks_raw(metal_status.error)
        ):
            # A launcher that keeps the raw output apart writes `error` for a
            # person; it is shown as it is.
            reason = metal_status.error
        elif not was_ready and not metal_status.installed:
            # Failed before the GPU environment existed: the set-up, not the model.
            reason = failures.explain_gpu_setup(raw)
        else:
            reason = failures.explain(
                raw.splitlines(),
                gpu=True,
                has_token=self._has_token(),
                model_id=model_id,
                was_ready=was_ready,
            )

        self._release_metal()
        self._update_status(
            state=ServerState.ERROR,
            message=failures.crashed(model_id) if was_ready else failures.couldnt_start(model_id),
            error=reason,
            crashed=was_ready,
        )

    async def _run_deployment(
        self, request: DeployRequest, hf_cache_dir: Path, generation: int
    ) -> None:
        """Asynchronous runner that pulls images, starts container, and polls health.

        The generation captured at launch gates every write to shared state: once a
        newer deploy or stop supersedes this runner, it must fall silent instead of
        overwriting the state of whatever replaced it.
        """
        name = short_name(request.model_id)
        try:
            loop = asyncio.get_running_loop()

            # 1. Ask the runtime what to run. Everything model- and backend-specific
            # is decided here and nowhere else in this method.
            spec = await self._runtime.container_spec(
                request,
                SpecContext(
                    docker_memory_bytes=await self._docker_memory_bytes(),
                    hf_token=settings.HUGGINGFACE_API_KEY or None,
                ),
            )
            image_name = spec.image

            # 2. Check / Pull Docker Image with streaming progress
            self._update_status(
                state=ServerState.PULLING_IMAGE,
                message=f"Getting ready to start {name}…",
            )
            self._add_log(f"ℹ Checking for {image_name}...")

            def _local_image() -> Optional[Any]:
                try:
                    return self._client.images.get(image_name)
                except Exception:
                    return None

            local_image = await loop.run_in_executor(None, _local_image)
            stale = local_image is not None and self._is_from_another_release(local_image)
            if local_image is None or stale:
                if stale:
                    self._add_log(f"ℹ Updating {image_name} to match this version of Kayak...")
                else:
                    self._add_log(f"ℹ {image_name} is not on this Mac yet; downloading it...")
                self._update_status(
                    state=ServerState.PULLING_IMAGE,
                    message=ENGINE_DOWNLOAD_MESSAGE,
                )

                try:
                    await loop.run_in_executor(
                        None, self._stream_pull, image_name, loop, generation
                    )
                except Exception as error:
                    if self._superseded(generation):
                        return
                    logger.warning("Downloading %s failed: %s", image_name, error)
                    self._add_log(f"✗ Downloading {image_name} failed: {error}")
                    if not stale:
                        raise await self._engine_download_failure(error)
                    # Offline, most likely. The older image still serves.
                    self._add_log(f"ℹ Using the copy of {image_name} already on this Mac.")
                else:
                    if self._superseded(generation):
                        return
                    # Docker's stream can end without an error line and without the
                    # image -- it is checked rather than assumed, or the container
                    # start below pulls it again silently and fails with a 404.
                    pulled = await loop.run_in_executor(None, _local_image)
                    if pulled is None:
                        if not stale:
                            raise failures.StartFailure(
                                failures.ENGINE_OFFLINE,
                                detail=f"{image_name} was not on this Mac after downloading it.",
                            )
                    else:
                        self._add_log(f"✓ Downloaded {image_name}.")
                        # Only an image that was actually replaced is removed: a
                        # failed or unchanged download leaves the old one as the
                        # only copy that works.
                        if stale and pulled.id != local_image.id:
                            await loop.run_in_executor(None, self._remove_image, local_image.id)

            # 3. Report what the runtime decided, so its choices are visible in the
            # log rather than implicit in the container's arguments.
            for note in spec.notes:
                self._add_log(note)

            if request.memory_limit_gb or request.cpu_limit:
                limits = []
                if request.memory_limit_gb:
                    limits.append(f"{request.memory_limit_gb:g} GiB of memory")
                if request.cpu_limit:
                    limits.append(f"{request.cpu_limit:g} processor cores")
                self._add_log(f"ℹ Limited to {' and '.join(limits)}.")

            self._update_status(
                state=ServerState.LOADING,
                message=f"Downloading and loading {name}… The first start can take a while.",
            )
            self._add_log(f"ℹ Starting the {self._runtime.key} server for {request.model_id}")

            # 4. Start the container
            def _start_container():
                # Forcibly remove any existing container with the same name
                try:
                    old_c = self._client.containers.get(self._runtime.container_name)
                    old_c.remove(force=True)
                except Exception:
                    pass

                if self._superseded(generation):
                    return None, None

                volumes = {
                    docker_host.host_path(hf_cache_dir): {
                        "bind": "/root/.cache/huggingface",
                        "mode": "rw",
                    }
                }

                run_kwargs: Dict[str, Any] = {
                    "image": image_name,
                    "name": self._runtime.container_name,
                    "command": spec.command,
                    "detach": True,
                    "volumes": volumes,
                    "environment": spec.environment,
                    "remove": False,
                    **spec.run_kwargs,
                }

                # Container-level ceilings, chosen by the user. Applied here rather
                # than by the runtime: they mean the same thing whatever is being
                # served, and a runtime must not be able to ignore them. Unset means
                # the container may use everything the Docker VM has.
                if request.memory_limit_gb:
                    run_kwargs["mem_limit"] = f"{int(request.memory_limit_gb * 1024)}m"
                if request.cpu_limit:
                    run_kwargs["nano_cpus"] = int(request.cpu_limit * 1_000_000_000)

                # The configured port first, then neighbours. On a launcher
                # install Kayak itself is published on the default vLLM port, so
                # binding it fails with "port is already allocated" -- which used
                # to end the deployment instead of trying the port next door.
                # A second modality's server is another way the port next door is
                # already taken, so the fallback matters more now, not less.
                last_port_error: Optional[Exception] = None
                for offset in range(self.PORT_FALLBACK_ATTEMPTS):
                    port = self._runtime.default_port + offset
                    run_kwargs["ports"] = {
                        f"{self._runtime.container_port}/tcp": port
                    }
                    try:
                        created = self._client.containers.run(**run_kwargs)
                    except APIError as error:
                        message = str(error).lower()
                        if not any(marker in message for marker in self._PORT_TAKEN_MARKERS):
                            raise
                        last_port_error = error
                        # Docker creates the container before failing to bind its
                        # port; it must go before the name can be reused.
                        try:
                            self._client.containers.get(self._runtime.container_name).remove(force=True)
                        except Exception:
                            pass
                        loop.call_soon_threadsafe(
                            self._add_log,
                            f"ℹ Port {port} is taken by another application; trying {port + 1}...",
                        )
                        continue

                    # A stop that raced this thread has already done its removal,
                    # so a container created after that point would outlive the
                    # "stopped" status unnoticed. The generation bump happens
                    # before the stop's removal, so checking after creation
                    # closes both orderings.
                    if self._superseded(generation):
                        try:
                            created.remove(force=True)
                        except Exception:
                            pass
                        return None, None
                    return created, port

                raise failures.StartFailure(
                    failures.PORT_TAKEN,
                    detail=(
                        f"No free port between {self._runtime.default_port} and "
                        f"{self._runtime.default_port + self.PORT_FALLBACK_ATTEMPTS - 1} to "
                        f"publish the server on. ({last_port_error})"
                    ),
                )

            container, chosen_port = await loop.run_in_executor(None, _start_container)
            if container is None or chosen_port is None or self._superseded(generation):
                return
            self._status.container_id = container.id
            self._status.port = chosen_port
            self._status.endpoint = self._api_base_for_port(chosen_port)
            self._add_log(f"ℹ Server started (container {container.id[:12]}).")
            if chosen_port != self._runtime.default_port:
                self._add_log(
                    f"ℹ Serving on port {chosen_port} because "
                    f"{self._runtime.default_port} is in use."
                )

            # 5. Spawn background non-blocking daemon thread for container log streaming
            if self._log_stop_event:
                self._log_stop_event.set()
            self._log_stop_event = threading.Event()
            self._spawn_log_stream_thread(container, loop, self._log_stop_event)

            # 6. Poll health endpoint until healthy (non-blocking). A failure the
            # runtime knows how to fix is retried from in there, before it is shown.
            await self._poll_health_endpoint(request.model_id, generation, chosen_port)

        except failures.StartFailure as failure:
            if self._superseded(generation):
                return
            if failure.detail:
                logger.warning("Starting %s failed: %s", request.model_id, failure.detail)
                self._add_log(f"✗ {failure.detail}")
            self._update_status(
                state=ServerState.ERROR,
                message=failures.couldnt_start(request.model_id),
                error=failure.message,
            )
        except Exception as error:
            if self._superseded(generation):
                return
            logger.exception("Starting %s failed", request.model_id)
            self._add_log(f"✗ {error}")
            if isinstance(error, _CONNECTION_FAILURES) and not await self._docker_alive():
                reason = failures.DOCKER_STOPPED
            else:
                reason = self._explain()
            if self._superseded(generation):
                return
            self._update_status(
                state=ServerState.ERROR,
                message=failures.couldnt_start(request.model_id),
                error=reason,
            )

    def _stream_pull(
        self, image_name: str, loop: asyncio.AbstractEventLoop, generation: int
    ) -> None:
        """Downloads an image, reporting overall progress as a percentage.

        Runs in a worker thread. Docker reports a failed download as a line in the
        stream rather than as an exception, so every line is checked: ignoring them
        logged "downloaded" for a download that had failed.

        Raises:
            RuntimeError: With Docker's own message, when the stream reports an error.
        """
        layers: Dict[str, Tuple[int, int]] = {}
        shown = -1

        for line in self._client.api.pull(image_name, stream=True, decode=True):
            # A stop or replacement mid-pull must silence this stream;
            # the download itself is harmless and simply caches layers.
            if self._superseded(generation):
                return
            if line.get("error") or line.get("errorDetail"):
                detail = (line.get("errorDetail") or {}).get("message") or line.get("error")
                raise RuntimeError(str(detail or "the download failed"))

            status_str = line.get("status", "")
            layer_id = line.get("id", "")
            detail = line.get("progressDetail") or {}

            if layer_id and status_str == "Downloading" and detail.get("total"):
                layers[layer_id] = (int(detail.get("current") or 0), int(detail["total"]))
            elif layer_id in layers and status_str in ("Download complete", "Pull complete"):
                total = layers[layer_id][1]
                layers[layer_id] = (total, total)

            if layers:
                total = sum(size for _, size in layers.values())
                done = sum(current for current, _ in layers.values())
                percent = max(shown, min(100, int(done * 100 / total))) if total else shown
                if percent != shown:
                    shown = percent
                    loop.call_soon_threadsafe(self._note_engine_download, percent, generation)

            # Byte counts are summarised above; logging each one buried the lines
            # that say what happened under hundreds of near-identical ones.
            if status_str in ("Downloading", "Extracting") and detail:
                continue
            message = f"[{layer_id}] {status_str}" if layer_id else status_str
            if message.strip():
                loop.call_soon_threadsafe(self._add_log, message.strip())

    def _note_engine_download(self, percent: int, generation: int) -> None:
        if self._superseded(generation) or self._status.state != ServerState.PULLING_IMAGE:
            return
        self._update_status(
            state=ServerState.PULLING_IMAGE,
            message=f"{ENGINE_DOWNLOAD_MESSAGE} {percent}%",
        )

    async def _engine_download_failure(self, error: Exception) -> failures.StartFailure:
        """What to report when the image that runs models could not be downloaded."""
        if isinstance(error, _CONNECTION_FAILURES) and not await self._docker_alive():
            return failures.StartFailure(failures.DOCKER_STOPPED, detail=str(error))
        return failures.StartFailure(
            failures.explain_engine_download(str(error)), detail=str(error)
        )

    def _is_from_another_release(self, image: Any) -> bool:
        """Whether a Kayak-published image was built for a different Kayak release."""
        if not self._runtime.released_with_kayak or settings.VERSION == "dev":
            return False
        version = (image.labels or {}).get("org.opencontainers.image.version")
        return bool(version) and version != settings.VERSION

    def _remove_image(self, image_id: str) -> None:
        """Deletes an image a pull has superseded, so updates do not pile up gigabytes."""
        try:
            self._client.images.remove(image_id)
        except Exception:
            # Still in use, or already gone; either way there is nothing to reclaim.
            pass

    def _schedule_retry(self, retry: DeployRequest) -> None:
        """Starts a runtime's self-healing retry once the failed start has unwound."""
        generation = self._deploy_generation
        self._retry_task = asyncio.create_task(self._redeploy_retry(retry, generation))

    async def _redeploy_retry(self, retry: DeployRequest, generation: int) -> None:
        """Runs a runtime's self-healing retry, reporting what it changed.

        Abandoned if anything happened in between: a Stop clicked while the retry
        waited for the lock used to be followed by the model starting again.
        """
        try:
            async with self._control_lock:
                if self._superseded(generation):
                    return
                await self._deploy_locked(retry)
            if retry.max_model_len is not None:
                self._add_log(
                    f"ℹ The model's default context does not fit in the memory it has; "
                    f"restarted automatically with a {retry.max_model_len}-token context."
                )
            if retry.tool_parser is not None:
                self._add_log(
                    f"ℹ This model's tool-call format was not recognised; restarted "
                    f"automatically with the {retry.tool_parser} format."
                )
        except Exception:
            logger.exception("Automatic deployment retry failed to start")

    def _spawn_log_stream_thread(
        self,
        container: Any,
        loop: asyncio.AbstractEventLoop,
        stop_event: threading.Event,
    ) -> None:
        """Streams container logs in a separate OS thread to avoid blocking asyncio event loop."""
        def _worker():
            try:
                for chunk in container.logs(stream=True, follow=True):
                    if stop_event.is_set():
                        break
                    text = chunk.decode("utf-8", errors="replace")
                    for line in text.splitlines():
                        clean = line.strip()
                        if clean and not stop_event.is_set():
                            loop.call_soon_threadsafe(self._add_log, clean)
            except Exception:
                pass
            finally:
                # Clear the manager's handle so a later container gets a streamer.
                # _ensure_log_streamer treats a non-None event as "already streaming",
                # so leaving a finished stream's event in place would silence logs for
                # every container discovered afterwards.
                if self._log_stop_event is stop_event:
                    self._log_stop_event = None

        thread = threading.Thread(target=_worker, daemon=True, name=f"{self._runtime.key}-log-streamer")
        thread.start()

    async def _lookup_container(self) -> Tuple[Optional[Any], bool]:
        """This runtime's container with fresh attributes, and whether Docker answered.

        "No container" means two different things -- it is gone, or Docker is --
        and they are reported differently, so the second value says which.
        """
        if not self._client:
            return None, False
        client = self._client
        name = self._runtime.container_name

        def _get() -> Tuple[Optional[Any], Optional[bool]]:
            try:
                return client.containers.get(name), True
            except NotFound:
                return None, True
            except Exception:
                return None, None

        loop = asyncio.get_running_loop()
        container, answered = await loop.run_in_executor(None, _get)
        if answered is None:
            answered = await self._docker_alive()
        return container, answered

    async def _get_container(self) -> Optional[Any]:
        """Fetches this runtime's container with fresh attributes, or None if gone."""
        if not self._docker_available or not self._client:
            return None
        container, _answered = await self._lookup_container()
        return container

    @staticmethod
    def _read_exit(container: Any) -> Tuple[Optional[int], bool]:
        """Returns the container's exit code and whether the kernel OOM-killed it."""
        state = (getattr(container, "attrs", {}) or {}).get("State", {}) or {}
        exit_code = state.get("ExitCode")
        return (
            exit_code if isinstance(exit_code, int) else None,
            bool(state.get("OOMKilled")),
        )

    def _report_container_exit(
        self, exit_code: Optional[int], oom_killed: bool, was_ready: bool = False
    ) -> None:
        """Moves status to ERROR after the container stopped on its own.

        The headline says which of two things happened -- the model never started,
        or it was serving and then stopped -- and the explanation underneath is
        read from its output by :func:`failures.explain`. The raw line that named
        the cause goes to the log, where Details shows it.

        A failure the runtime knows how to fix is retried instead of reported: the
        model restarts with settings that fit, and the user never sees the error.
        """
        model_id = self._status.model_id
        reason = extract_failure_reason(self._log_history)
        logger.warning(
            "%s for %s stopped (exit code %s, out of memory: %s): %s",
            self._runtime.key, model_id, exit_code, oom_killed, reason,
        )
        self._add_log(f"✗ The server stopped (exit code {exit_code}).")

        if not was_ready and self._active_request is not None:
            retry = self._runtime.retry_request(
                self._active_request,
                self._status.model_copy(update={"state": ServerState.ERROR}),
                self._log_history,
            )
            if retry is not None:
                self._update_status(
                    state=ServerState.LOADING,
                    message=f"Restarting {short_name(model_id)} with settings that fit this Mac…",
                )
                self._schedule_retry(retry)
                return

        self._update_status(
            state=ServerState.ERROR,
            message=failures.crashed(model_id) if was_ready else failures.couldnt_start(model_id),
            error=self._explain(exit_code, oom_killed, was_ready=was_ready),
            exit_code=exit_code if exit_code is not None else -1,
            crashed=was_ready,
        )

    def _endpoint_serves_model(self, payload: Any, model_id: str) -> bool:
        """Whether a health response says the requested model is being served.

        A 200 alone is not proof of readiness: during a backend switch the previous
        server -- a Metal process winding down, or an older container -- can still be
        answering on the same port, and another modality's server runs on the port
        next door. The runtime decides what counts as its own model.
        """
        return self._runtime.serves_model(payload, model_id)

    async def _poll_health_endpoint(
        self,
        model_id: str,
        generation: Optional[int] = None,
        port: Optional[int] = None,
        timeout_seconds: int = START_TIMEOUT_CAP_SECONDS,
    ) -> None:
        """Waits for the endpoint to serve this model, or for the container to die.

        This is the sole mechanism for transitioning a container to READY. It gives
        up after a long silence from the server, or at an overall cap, and it only
        accepts an answer that names the model being deployed.
        """
        if generation is None:
            generation = self._deploy_generation
        port = port or self._status.port or self._runtime.default_port
        started = time.monotonic()
        next_container_check = time.monotonic() + CONTAINER_CHECK_INTERVAL_SECONDS
        next_download_check = time.monotonic() + DOWNLOAD_CHECK_INTERVAL_SECONDS
        downloaded = await asyncio.to_thread(_downloaded_bytes, model_id)

        async with httpx.AsyncClient(timeout=STARTING_CHECK_TIMEOUT_SECONDS) as client:
            while not self._start_overdue(started, timeout_seconds):
                await asyncio.sleep(1.0)

                # Bail if deployment was cancelled, errored, or replaced
                if self._superseded(generation) or self._status.state in (
                    ServerState.ERROR,
                    ServerState.STOPPED,
                    ServerState.IDLE,
                ):
                    return

                try:
                    res = await client.get(self._health_url(port))
                    if res.status_code == 200 and self._endpoint_serves_model(
                        res.json(), model_id
                    ):
                        if self._superseded(generation):
                            return
                        self._ready_failures = 0
                        self._update_status(
                            state=ServerState.READY,
                            message=f"{short_name(model_id)} is ready.",
                        )
                        return
                except Exception:
                    pass

                if time.monotonic() >= next_download_check:
                    next_download_check = time.monotonic() + DOWNLOAD_CHECK_INTERVAL_SECONDS
                    now_downloaded = await asyncio.to_thread(_downloaded_bytes, model_id)
                    if now_downloaded > downloaded:
                        self._last_activity = time.monotonic()
                    downloaded = now_downloaded

                # A crashed container will never answer, so waiting out the full
                # deadline would report a timeout for a failure that already happened.
                if time.monotonic() >= next_container_check:
                    next_container_check = time.monotonic() + CONTAINER_CHECK_INTERVAL_SECONDS
                    container, docker_answered = await self._lookup_container()
                    if self._superseded(generation):
                        return
                    if container is None:
                        self._update_status(
                            state=ServerState.ERROR,
                            message=failures.couldnt_start(model_id),
                            error=(
                                failures.interrupted(model_id)
                                if docker_answered
                                else failures.DOCKER_STOPPED
                            ),
                        )
                        return
                    if container.status not in ("running", "created", "restarting"):
                        exit_code, oom_killed = self._read_exit(container)
                        self._report_container_exit(exit_code, oom_killed)
                        return

        if self._superseded(generation):
            return
        await self._give_up_on_container_start(model_id, generation)

    def _start_overdue(self, started: float, cap_seconds: float) -> bool:
        """Whether a start has run past its cap, or gone quiet for too long."""
        now = time.monotonic()
        return (
            now - started >= cap_seconds
            or now - self._last_activity >= START_IDLE_TIMEOUT_SECONDS
        )

    async def _give_up_on_container_start(self, model_id: str, generation: int) -> None:
        """Ends a start that ran out of time: removes its container, then says so.

        A timeout used to report the failure and leave the container running,
        holding its memory with nothing waiting for it.
        """
        self._add_log("✗ The model did not finish starting in time; stopping it.")
        if self._log_stop_event:
            self._log_stop_event.set()
            self._log_stop_event = None
        await self._remove_container()
        if self._superseded(generation):
            return
        self._update_status(
            state=ServerState.ERROR,
            message=failures.couldnt_start(model_id),
            error=failures.TIMED_OUT,
        )

    async def _remove_container(self) -> None:
        """Stops and removes this runtime's container, if Docker has one."""
        if not self._docker_available or not self._client:
            return
        client = self._client
        name = self._runtime.container_name

        def _stop():
            try:
                container = client.containers.get(name)
                container.stop(timeout=3)
                container.remove(force=True)
            except Exception:
                pass

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _stop)

    async def _watch_adopted_start(self, model_id: str, port: int, generation: int) -> None:
        """Watches a start this process found already under way, after a restart.

        It gets the same deadline as a start this process began: adopted without
        one, a container that never came up stayed "Starting" indefinitely.
        """
        try:
            await self._poll_health_endpoint(model_id, generation, port)
        except asyncio.CancelledError:
            pass

    async def stop_server(self) -> None:
        """Stops and removes this runtime's container, or its Metal server."""
        async with self._control_lock:
            await self._stop_locked()

    async def _stop_locked(self, announce: bool = True) -> None:
        """Stops whatever this runtime is running.

        Args:
            announce: Whether to broadcast the STOPPED status. A start replacing the
                current model announces its own status straight away; announcing
                the stop in between made every restart flash "not running".
        """
        # Supersede any in-flight deployment first: its monitors must stop writing
        # status before this method starts rewriting it.
        self._deploy_generation += 1
        self._active_request = None
        self._ready_failures = 0

        if self._log_stop_event:
            self._log_stop_event.set()
            self._log_stop_event = None

        if self._metal_monitor_task and not self._metal_monitor_task.done():
            self._metal_monitor_task.cancel()
            try:
                await self._metal_monitor_task
            except asyncio.CancelledError:
                pass
            self._metal_monitor_task = None

        # Written whenever a launcher has ever reported here, not only when the
        # current status says supported: a stale or unreadable status must not
        # leave "keep running" on disk for the launcher to act on later.
        if self._runtime.supports_metal:
            metal_status = metal.read_status()
            if (
                metal_status.supported
                or metal.status_path().exists()
                or metal.desired_path().exists()
            ):
                self._release_metal()

        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None

        await self._remove_container()

        model_id = self._status.model_id
        self._status.state = ServerState.STOPPED
        self._status.message = f"{short_name(model_id)} stopped." if model_id else "Not running."
        self._status.error = None
        self._status.exit_code = None
        self._status.crashed = False
        self._status.container_id = None
        if announce:
            self._broadcast_status()
            self._add_log(f"ℹ {self._status.message}")

    async def check_and_sync_status(self) -> DeploymentProgress:
        """Inspects Docker and probes the vLLM HTTP endpoint to synchronize internal state.

        While any deployment monitor is in flight, that monitor owns the status and
        this method reports without probing: reconciling against a reality that is
        mid-transition is how an in-flight deploy got overwritten by the state of the
        server it was replacing.
        """
        if self._deployment_in_flight():
            self._status.logs_tail = self._log_history[-30:]
            return self._status

        async with self._control_lock:
            # A deploy may have started while this call waited on the lock.
            if self._deployment_in_flight():
                self._status.logs_tail = self._log_history[-30:]
                return self._status
            return await self._sync_locked()

    def _deployment_in_flight(self) -> bool:
        return bool(
            (self._monitor_task and not self._monitor_task.done())
            or (self._metal_monitor_task and not self._metal_monitor_task.done())
        )

    def _on_metal(self) -> bool:
        """Whether the model this runtime holds is one the launcher serves."""
        return bool(
            self._runtime.supports_metal
            and self._status.model_id
            and metal.is_mlx_model(self._status.model_id)
        )

    async def _sync_locked(self) -> DeploymentProgress:
        """Reconciles the status with reality, in one direction only.

        Reality may end a model -- a crash, a vanished container, a server that no
        longer answers -- and may complete a start nothing is watching. It may not
        bring back a model the user stopped or one that already failed: the
        launcher and Docker both lag a stop by seconds, and reading their
        not-yet-updated state as "running" is how a stopped model came back.
        """
        if self._runtime.supports_metal and self._sync_metal_locked():
            self._status.logs_tail = self._log_history[-30:]
            return self._status
        await self._sync_container_locked()
        self._status.logs_tail = self._log_history[-30:]
        return self._status

    def _adopt_metal_start(self, model_id: str, request_token: Optional[str]) -> None:
        """Watches a GPU start this process did not begin, with the usual deadline."""
        self._deploy_generation += 1
        self._last_activity = time.monotonic()
        self._metal_monitor_task = asyncio.create_task(
            self._run_metal_deployment(model_id, request_token, self._deploy_generation)
        )

    def _sync_metal_locked(self) -> bool:
        """Reconciles against the launcher. Returns True when that settled the status.

        The launcher's status is only believed when it answers what Kayak asked
        for: a run is wanted, for that model, and -- when the launcher echoes
        requests -- for that request. Right after a stop the status file still says
        "ready" for a couple of seconds, and believing it brought the model back.
        """
        desired = metal.read_desired()
        metal_status = metal.read_status()
        state = self._status.state

        wanted = bool(
            metal_status.supported
            and desired.running
            and desired.model
            and metal_status.model == desired.model
            and (
                not metal_status.acknowledges_requests
                or metal_status.request == desired.request
            )
        )

        if wanted:
            model_id = metal_status.model
            name = short_name(model_id)

            if metal_status.state == "ready":
                if state == ServerState.IDLE or state in _LOADING_STATES:
                    self._status.model_id = model_id
                    self._status.port = metal_status.port or self._runtime.default_port
                    self._status.endpoint = self._api_base_for_port(self._status.port)
                    self._status.container_id = None
                    self._ready_failures = 0
                    self._update_status(state=ServerState.READY, message=f"{name} is ready.")
                    return True
                # Serving already: the endpoint check decides whether it still is.
                return False

            if metal_status.state == "error":
                if state in _ACTIVE_STATES and self._status.model_id == model_id:
                    # READY included: a Metal server can die after coming up, and
                    # reporting that as a quiet "stopped" hid the launcher's reason.
                    self._report_metal_error(
                        model_id, metal_status, was_ready=state == ServerState.READY
                    )
                return True

            if metal_status.state in ("installing", "starting"):
                # Adopted from IDLE only, i.e. after a restart of this process. A
                # model that is STOPPED or failed stays that way, whatever the
                # launcher is still winding down.
                if state == ServerState.IDLE:
                    self._status.model_id = model_id
                    # The real port is taken when the launcher reports "ready".
                    self._status.port = self._runtime.default_port
                    self._status.endpoint = self._api_base_for_port(self._status.port)
                    self._status.container_id = None
                    self._active_request = None
                    self._update_status(
                        state=ServerState.LOADING,
                        message=(
                            "Setting up GPU support (one time, a few minutes)…"
                            if metal_status.state == "installing"
                            else f"Loading {name} on your Mac's GPU…"
                        ),
                    )
                    self._adopt_metal_start(model_id, desired.request)
                elif state in _LOADING_STATES and self._status.model_id == model_id:
                    self._adopt_metal_start(model_id, desired.request)
                return True

        # A GPU start this process announced but is no longer watching -- its
        # monitor ended without settling it -- is watched again rather than left
        # "Starting" with nothing to finish it. The monitor also notices a launcher
        # that has stopped reporting, which this check cannot.
        if (
            state in _LOADING_STATES
            and desired.running
            and desired.model
            and desired.model == self._status.model_id
        ):
            self._adopt_metal_start(desired.model, desired.request)
            return True

        return False

    async def _sync_container_locked(self) -> None:
        # 1. Check Docker container state if docker client is available. The
        # container is read before probing so the probe targets the port the
        # container actually publishes -- after a backend restart it may not be
        # the configured default.
        container = None
        docker_answered = False
        if await self._ensure_docker():
            container, docker_answered = await self._lookup_container()

        state = self._status.state
        on_metal = self._on_metal()
        # A GPU model is served on the launcher's port; a container left over from
        # an earlier CPU model must not redirect the check to its own.
        active_port = (
            (
                self._container_host_port(container)
                if container is not None and not on_metal
                else None
            )
            or self._status.port
            or self._runtime.default_port
        )
        running = container is not None and container.status == "running"

        # 2. Probe the HTTP endpoint to see if the server is responding
        served: Any = None
        is_endpoint_alive = False
        timeout = (
            READY_CHECK_TIMEOUT_SECONDS
            if state == ServerState.READY
            else STARTING_CHECK_TIMEOUT_SECONDS
        )
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                resp = await client.get(self._health_url(active_port))
                if resp.status_code == 200:
                    served = resp.json()
                    is_endpoint_alive = True
            except Exception:
                pass

        if state == ServerState.READY:
            await self._check_ready(container, docker_answered, on_metal, served, is_endpoint_alive)
            return

        # Declared stopped after going unanswered, but answering again: a Mac that
        # was busy rather than a model that died. The container was kept for this.
        if (
            state == ServerState.ERROR
            and self._status.crashed
            and not on_metal
            and running
            and is_endpoint_alive
            and self._status.model_id
            and self._endpoint_serves_model(served, self._status.model_id)
        ):
            self._ready_failures = 0
            self._add_log("✓ The server is answering again.")
            self._update_status(
                state=ServerState.READY,
                message=f"{short_name(self._status.model_id)} is ready.",
            )
            return

        if state in _LOADING_STATES:
            await self._check_unwatched_start(
                container, docker_answered, on_metal, served, is_endpoint_alive, active_port
            )
            return

        if state == ServerState.IDLE and running:
            # A container from before this process started -- the backend restarted
            # while a model was serving or loading. Adopted; it is ours by name.
            model_name = self._model_of_container(container)
            self._status.model_id = model_name
            self._status.port = active_port
            self._status.endpoint = self._api_base_for_port(active_port)
            self._status.container_id = container.id
            self._ensure_log_streamer(container)
            if is_endpoint_alive and self._endpoint_serves_model(served, model_name):
                self._ready_failures = 0
                self._update_status(
                    state=ServerState.READY, message=f"{short_name(model_name)} is ready."
                )
            else:
                self._last_activity = time.monotonic()
                self._update_status(
                    state=ServerState.LOADING, message=f"Loading {short_name(model_name)}…"
                )
                self._deploy_generation += 1
                self._monitor_task = asyncio.create_task(
                    self._watch_adopted_start(model_name, active_port, self._deploy_generation)
                )

        # STOPPED and ERROR are left as they are. A server still answering, or a
        # container still running, is one being wound down -- not a reason to show
        # a model the user stopped as running again.

    async def _check_ready(
        self,
        container: Any,
        docker_answered: bool,
        on_metal: bool,
        served: Any,
        is_endpoint_alive: bool,
    ) -> None:
        """Keeps a ready model ready until it is plainly gone.

        Gone means its container exited or vanished, or several checks in a row
        went unanswered. A single slow answer -- a model busy writing a long reply
        on the CPU -- used to drop it back to "Starting".
        """
        model_id = self._status.model_id

        if not on_metal and container is not None and container.status != "running":
            exit_code, oom_killed = self._read_exit(container)
            if exit_code:
                self._report_container_exit(exit_code, oom_killed, was_ready=True)
            else:
                # Exit code zero is an orderly shutdown, not a failure: something
                # outside Kayak stopped it on purpose.
                self._status.state = ServerState.STOPPED
                self._status.message = f"{short_name(model_id)} stopped."
                self._status.container_id = None
                self._broadcast_status()
            return

        if not on_metal and container is None and docker_answered:
            self._add_log("✗ The server is no longer there.")
            self._update_status(
                state=ServerState.ERROR,
                message=failures.crashed(model_id),
                error=failures.stopped_unexpectedly(model_id),
                crashed=True,
            )
            return

        if is_endpoint_alive and (not model_id or self._endpoint_serves_model(served, model_id)):
            self._ready_failures = 0
            if container is not None and container.status == "running":
                self._ensure_log_streamer(container)
            return

        self._ready_failures += 1
        if self._ready_failures < READY_FAILURES_BEFORE_ERROR:
            return

        self._ready_failures = 0
        self._add_log("✗ The server stopped answering.")
        if on_metal:
            self._release_metal()
            error = failures.stopped_unexpectedly(model_id)
        elif not docker_answered:
            error = (
                f"Docker Desktop stopped, so {short_name(model_id)} stopped too. Open "
                "Docker Desktop, then start it again from Local Models."
            )
        else:
            error = failures.stopped_unexpectedly(model_id)
        self._update_status(
            state=ServerState.ERROR,
            message=failures.crashed(model_id),
            error=error,
            crashed=True,
        )

    async def _check_unwatched_start(
        self,
        container: Any,
        docker_answered: bool,
        on_metal: bool,
        served: Any,
        is_endpoint_alive: bool,
        active_port: int,
    ) -> None:
        """Settles a start that no monitor is watching any more.

        Nothing should be in this state for long -- every start has a monitor -- but
        a start whose monitor ended without settling it used to stay "Starting"
        forever, every Start button disabled behind it.
        """
        model_id = self._status.model_id

        if container is not None and container.status == "running":
            if model_id and is_endpoint_alive and self._endpoint_serves_model(served, model_id):
                self._ready_failures = 0
                self._update_status(
                    state=ServerState.READY, message=f"{short_name(model_id)} is ready."
                )
                return
            # Still coming up: watched again, with a deadline.
            self._ensure_log_streamer(container)
            self._last_activity = time.monotonic()
            self._deploy_generation += 1
            self._monitor_task = asyncio.create_task(
                self._watch_adopted_start(
                    model_id or self._model_of_container(container),
                    active_port,
                    self._deploy_generation,
                )
            )
            return

        if container is not None:
            exit_code, oom_killed = self._read_exit(container)
            self._report_container_exit(exit_code, oom_killed)
            return

        if is_endpoint_alive and model_id and self._endpoint_serves_model(served, model_id):
            self._ready_failures = 0
            self._update_status(
                state=ServerState.READY, message=f"{short_name(model_id)} is ready."
            )
            return

        # No container, nothing answering, and -- had a GPU run been wanted for this
        # model, the launcher check would have taken it -- nothing left to finish it.
        if on_metal:
            self._release_metal()
        self._update_status(
            state=ServerState.ERROR,
            message=failures.couldnt_start(model_id),
            error=(
                failures.DOCKER_STOPPED
                if not on_metal and not docker_answered
                else failures.interrupted(model_id)
            ),
        )

    def _model_of_container(self, container: Any) -> str:
        """The model a discovered container was started with, read from its command."""
        model_name = self._status.model_id or f"{self._runtime.key}-model"
        cmd = (getattr(container, "attrs", {}) or {}).get("Config", {}).get("Cmd", []) or []
        if "--model" in cmd:
            index = cmd.index("--model")
            if index + 1 < len(cmd):
                model_name = cmd[index + 1]
        return model_name

    async def _docker_resources(self) -> Tuple[Optional[int], Optional[int]]:
        """Returns (memory bytes, CPU count) as Docker reports them, or Nones.

        These are Docker Desktop's VM allocation rather than the Mac's hardware,
        which is exactly what bounds a container.
        """
        if not self._docker_available or not self._client:
            return None, None
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, docker_host.vm_resources, self._client)

    async def _docker_memory_bytes(self) -> Optional[int]:
        """Returns the memory Docker reports for its VM, or None if unavailable."""
        memory, _cpus = await self._docker_resources()
        return memory

    async def get_host_capability(self) -> HostCapability:
        """Reports what Docker can give this runtime's container, and whether its image is pulled."""
        image_present: Optional[bool] = None

        if await self._ensure_docker():
            def _has_image() -> Optional[bool]:
                try:
                    self._client.images.get(self._runtime.image)
                    return True
                except (ImageNotFound, NotFound):
                    return False
                except Exception:
                    return None

            loop = asyncio.get_running_loop()
            image_present = await loop.run_in_executor(None, _has_image)

        total_memory, total_cpus = await self._docker_resources()
        capability = HostCapability(
            docker_available=self._docker_available,
            total_memory_mb=int(total_memory / (1024 ** 2)) if total_memory else 0,
            total_cpus=total_cpus or 0,
            image_present=image_present,
        )
        self._runtime.augment_capability(capability, total_memory)
        return capability

    def is_serving(self, repo_id: str) -> bool:
        """Reports whether the given repository is the one currently being served."""
        return bool(
            self._status.model_id == repo_id
            and self._status.state in _ACTIVE_STATES
        )

    def start_watchdog(self) -> None:
        """Starts the periodic reconcile loop, once, for the process lifetime.

        SSE only broadcasts on change, and nothing changes unless something looks.
        Without this loop, a model that crashed while serving kept showing as ONLINE
        in every tab until a page was reloaded, and a deployment interrupted by a
        backend restart never progressed past the state it was rediscovered in.
        """
        if self._watchdog_task and not self._watchdog_task.done():
            return
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())

    async def _watchdog_loop(self) -> None:
        while True:
            await asyncio.sleep(WATCHDOG_INTERVAL_SECONDS)
            try:
                if self._deployment_in_flight():
                    continue
                await self.check_and_sync_status()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Model server watchdog reconcile failed; will retry")

    async def shutdown(self) -> None:
        """Stops this runtime's monitors and its server container.

        The container used to be left running on purpose, so that a backend restart
        could re-adopt it instead of paying for the model load again. That trade is
        wrong for the app people actually run: closing the desktop app removes the
        Kayak container, and a speech or text server was left behind holding several
        gigabytes of memory with nothing left alive to talk to it, until the user went looking for it in Docker by hand.
        Loading the model again on the next launch is the cheaper mistake.

        The GPU server goes the same way, and first, because it is a single quick
        write: desired.json used to keep saying "running" after Kayak quit, and the
        launcher started the last GPU model again by itself at the next launch.

        No lock is taken. A deployment that is wedged holding the control lock must
        not be able to hold the whole shutdown past the daemon's grace period, and
        bumping the generation is enough to keep an in-flight deploy from leaving a
        container behind: the start path removes what it created once it sees it has
        been superseded.
        """
        self._release_metal()
        self._deploy_generation += 1
        self._active_request = None

        for task in (
            self._watchdog_task,
            self._monitor_task,
            self._metal_monitor_task,
            self._retry_task,
        ):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        if self._log_stop_event:
            self._log_stop_event.set()
            self._log_stop_event = None

        await self._stop_container_for_shutdown()

    async def _stop_container_for_shutdown(self) -> None:
        """Stops and removes this runtime's container, quietly and quickly.

        Found by name rather than by what this process started, so a container
        adopted from a previous run is stopped too.
        """
        if not self._docker_available or not self._client:
            return

        client = self._client
        name = self._runtime.container_name

        def _stop() -> None:
            try:
                container = client.containers.get(name)
            except Exception:
                # Nothing of this name: the usual case, and not worth a log line
                # on a process that is already on its way out.
                return
            try:
                # A shorter grace than a user-driven stop, because the whole
                # shutdown is racing the daemon's own timer and a model server
                # holds nothing that is not already on disk.
                container.stop(timeout=SHUTDOWN_STOP_TIMEOUT_SECONDS)
            except Exception:
                pass
            try:
                container.remove(force=True)
            except Exception:
                pass

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _stop)


#: Said when a model that only runs on the GPU is started where there is none.
APPLE_CHIP_REQUIRED = (
    "This model only runs on Macs with Apple chips (M1 or later). Pick a model without "
    "“mlx-community” in its name."
)


def metal_unavailable_message(metal_status: Any) -> str:
    """Why a GPU model cannot start here, in words for the person who tried.

    The launcher explains a capable Mac that still cannot serve the GPU -- an Intel
    build under Rosetta, say -- and Kayak explains a launcher that stopped reporting.
    Either is more useful than the generic sentence, when it is one a person can read.
    """
    detail = getattr(metal_status, "detail", None)
    if detail and not failures.looks_raw(detail):
        return detail
    return APPLE_CHIP_REQUIRED


def _downloaded_bytes(model_id: Optional[str]) -> int:
    """How much of a model is on disk so far, partial downloads included.

    Hugging Face keeps each repository under ``hub/models--Org--Name``, with the
    files of an unfinished download growing in place, so the total rises for as
    long as a download is making progress. Snapshot entries link to the same
    files, so links are not followed: each file is counted once.
    """
    if not model_id:
        return 0
    folder = (
        settings.DATA_DIR / "huggingface_cache" / "hub" / f"models--{model_id.replace('/', '--')}"
    )
    total = 0
    for root, _dirs, files in os.walk(folder):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                continue
    return total

