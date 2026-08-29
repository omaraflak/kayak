import asyncio
import logging
import re
import time
import uuid
from pathlib import Path
import shutil
import threading
from typing import Any, Dict, List, Optional, Set, Tuple
import docker
from docker.errors import APIError, NotFound, ImageNotFound
import httpx
from backend.app.config import settings
from backend.app.docker_utils import DockerPathResolver
from backend.app.inference import metal
from backend.app.inference.hardware import daemon_offers_gpu, probe_host_capability
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

# A first deployment downloads the image and then tens of gigabytes of weights, so the
# health check has to be patient. Genuine failures are caught by watching the container
# rather than by running out of attempts.
HEALTH_TIMEOUT_SECONDS = 1800
CONTAINER_CHECK_INTERVAL_SECONDS = 5.0

_ACTIVE_STATES = frozenset({
    ServerState.PULLING_IMAGE,
    ServerState.STARTING_CONTAINER,
    ServerState.LOADING,
    ServerState.READY,
})

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

def extract_failure_reason(log_lines: List[str]) -> Optional[str]:
    """Finds the line in a container's output that explains why it died.

    Quoting the last few lines instead is unreliable: a crashing vLLM prints shutdown
    tracebacks and interpreter warnings after the real error, so the tail is usually
    noise while the sentence naming the cause sits further up.

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
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
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
        #: Strong reference to an automatic context-length retry, so the task is
        #: not garbage collected mid-flight.
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

    def _idle_status(self) -> DeploymentProgress:
        """A status describing this runtime's server before anything has started."""
        port = self._runtime.default_port
        return DeploymentProgress(
            modality=self._runtime.modality,
            message=f"{self._runtime.server_label} is not running.",
            port=port,
            endpoint=self._runtime.api_base(port),
        )

    def _init_docker(self) -> None:
        try:
            self._client = docker.from_env()
            self._client.ping()
            self._docker_available = True
            DockerPathResolver.initialize(self._client)
        except Exception:
            self._client = None
            self._docker_available = False

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

    def _get_endpoint_urls(self, port: Optional[int] = None) -> List[str]:
        """Returns the ordered list of health endpoint URLs to probe.

        Probes the port the current deployment actually publishes, which may
        differ from the configured default when that port was taken.
        """
        port = port or self._status.port or self._runtime.default_port
        path = self._runtime.health_path()
        # The api base already carries the /v1 prefix that the health path repeats.
        api_base = self._api_base_for_port(port).rstrip("/")
        suffix = path[len("/v1"):] if path.startswith("/v1") else path
        urls = [
            f"http://host.docker.internal:{port}{path}",
            f"{api_base}{suffix}",
            f"http://localhost:{port}{path}",
            f"http://127.0.0.1:{port}{path}",
        ]
        # Deduplicated, order preserved: the api-base entry usually repeats one
        # of the fixed hosts.
        return list(dict.fromkeys(urls))

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

    def _add_log(self, line: str) -> None:
        """Appends a line to the logs buffer and broadcasts to subscribers."""
        clean_line = line.strip()
        if not clean_line:
            return
        self._log_history.append(clean_line)
        if len(self._log_history) > 300:
            self._log_history = self._log_history[-300:]

        self._status.logs_tail = self._log_history[-30:]
        self._broadcast({"type": "log", "line": clean_line})

    def _update_status(
        self,
        state: ServerState,
        message: str,
        error: Optional[str] = None,
        exit_code: Optional[int] = None,
    ) -> None:
        """Updates internal telemetry and broadcasts to frontend."""
        self._status.state = state
        self._status.message = message
        if error is not None:
            self._status.error = error
        if exit_code is not None:
            self._status.exit_code = exit_code

        self._broadcast({"type": "status", "data": self._status.model_dump()})

    def get_status(self) -> DeploymentProgress:
        """Returns the current deployment and server status."""
        self._status.logs_tail = self._log_history[-30:]
        return self._status

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
        # 1. Handle MLX models on Apple Silicon
        # Metal is a text-only path served by the launcher, not a container.
        # Runtimes that do not support it skip every branch below.
        if self._runtime.supports_metal and metal.is_mlx_model(request.model_id):
            metal_status = metal.read_status()
            if not metal_status.supported:
                self._update_status(
                    state=ServerState.ERROR,
                    message=f"Cannot deploy {request.model_id}: MLX models require Apple Silicon and the Kayak desktop launcher.",
                    error="Metal inference is not supported on this machine.",
                )
                return self.get_status()

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

            await self._stop_locked()

            generation = self._deploy_generation
            self._log_history.clear()
            self._status = DeploymentProgress(
                modality=self._runtime.modality,
                model_id=request.model_id,
                state=ServerState.STARTING_CONTAINER,
                message=f"Starting Metal server for {request.model_id}...",
                port=metal_status.port or self._runtime.default_port,
                endpoint=self._runtime.api_base(self._runtime.default_port),
            )
            self._active_request = request
            self._broadcast({"type": "status", "data": self._status.model_dump()})
            self._add_log(f"Requesting launcher to serve MLX model {request.model_id} on Apple GPU...")

            # The token lets the monitor tell a status answering this request
            # from a stale one: the launcher reconciles every couple of
            # seconds, and until it does, the status file still describes the
            # previous server — often as "ready".
            request_token = uuid.uuid4().hex
            metal.write_desired(running=True, model=request.model_id, request=request_token)

            self._metal_monitor_task = asyncio.create_task(
                self._run_metal_deployment(request.model_id, request_token, generation)
            )
            return self.get_status()

        # 2. For non-MLX models, stop Metal if it was, or may still be, running
        metal_status = metal.read_status()
        if self._runtime.supports_metal and (
            (metal_status.supported and metal_status.state != "stopped")
            or (not metal_status.supported and metal.status_path().exists())
        ):
            metal.write_desired(running=False)
            if self._metal_monitor_task and not self._metal_monitor_task.done():
                self._metal_monitor_task.cancel()

        if not await self._ensure_docker():
            self._update_status(
                state=ServerState.ERROR,
                message="Docker is not available on this system.",
                error="Docker daemon unreachable",
            )
            return self.get_status()

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

        # Stop existing deployment if running
        await self._stop_locked()

        generation = self._deploy_generation
        self._log_history.clear()
        self._status = DeploymentProgress(
            # Rebuilding the status must not silently relabel the server: without
            # this the field falls back to its default and a speech deployment
            # would broadcast itself as the text one.
            modality=self._runtime.modality,
            model_id=request.model_id,
            state=ServerState.STARTING_CONTAINER,
            message=f"Preparing environment for {request.model_id}...",
            port=self._runtime.default_port,
            endpoint=self._runtime.api_base(self._runtime.default_port),
        )
        self._active_request = request
        self._broadcast({"type": "status", "data": self._status.model_dump()})

        # Ensure HF cache directory on host
        hf_cache_dir = settings.DATA_DIR / "huggingface_cache"
        hf_cache_dir.mkdir(parents=True, exist_ok=True)

        self._monitor_task = asyncio.create_task(
            self._run_deployment(request, hf_cache_dir, generation)
        )
        return self.get_status()

    async def _run_metal_deployment(
        self,
        model_id: str,
        request_token: Optional[str] = None,
        generation: Optional[int] = None,
        timeout_seconds: int = HEALTH_TIMEOUT_SECONDS,
    ) -> None:
        """Monitors launcher status while starting a Metal server.

        Only statuses that answer *this* deployment are acted on. The launcher
        reconciles on a delay, so the first polls routinely read a file still
        describing the previous server — trusting its "ready" is exactly the
        bug where a model showed as serving the instant start was clicked.
        A launcher new enough to echo request tokens is matched on the token;
        an older one is matched on the model id, which still filters out
        statuses about a different model.
        """
        deadline = time.monotonic() + timeout_seconds
        if generation is None:
            generation = self._deploy_generation

        def answers_this_deployment(status) -> bool:
            if request_token and status.acknowledges_requests:
                return status.request == request_token
            return status.model == model_id

        try:
            while time.monotonic() < deadline:
                await asyncio.sleep(1.0)
                metal_status = metal.read_status()

                # Bail if deployment was cancelled, stopped, or replaced
                if self._superseded(generation) or self._status.state in (
                    ServerState.STOPPED,
                    ServerState.IDLE,
                ):
                    return

                if not answers_this_deployment(metal_status):
                    continue

                if metal_status.state == "ready":
                    self._status.port = metal_status.port or self._runtime.default_port
                    self._status.endpoint = self._runtime.api_base(self._runtime.default_port)
                    self._update_status(
                        state=ServerState.READY,
                        message=f"Metal server is healthy and serving {model_id}",
                    )
                    self._add_log(f"✓ Metal server is ready and serving {model_id}.")
                    return
                elif metal_status.state == "installing":
                    msg = f"Installing Metal environment for {model_id}..."
                    if self._status.message != msg:
                        self._update_status(state=ServerState.LOADING, message=msg)
                        self._add_log(f"Metal status: {msg}")
                elif metal_status.state == "starting":
                    msg = f"Starting Metal server for {model_id}..."
                    if self._status.message != msg:
                        self._update_status(state=ServerState.LOADING, message=msg)
                        self._add_log(f"Metal status: {msg}")
                elif metal_status.state == "error":
                    err_msg = metal_status.error or "Metal server encountered an error."
                    self._update_status(
                        state=ServerState.ERROR,
                        message=f"Failed to start Metal server for {model_id}",
                        error=err_msg,
                    )
                    self._add_log(f"✗ Metal server error: {err_msg}")
                    return

            if not self._superseded(generation):
                self._update_status(
                    state=ServerState.ERROR,
                    message=f"Metal server did not become ready within {timeout_seconds // 60} minutes",
                    error="Timed out waiting for launcher to start Metal server.",
                )
        except asyncio.CancelledError:
            pass

    async def _run_deployment(
        self, request: DeployRequest, hf_cache_dir: Path, generation: int
    ) -> None:
        """Asynchronous runner that pulls images, starts container, and polls health.

        The generation captured at launch gates every write to shared state: once a
        newer deploy or stop supersedes this runner, it must fall silent instead of
        overwriting the state of whatever replaced it.
        """
        try:
            loop = asyncio.get_running_loop()

            # 1. Ask the runtime what to run. Everything model- and backend-specific
            # is decided here and nowhere else in this method.
            has_gpu = await self._gpu_available()
            spec = await self._runtime.container_spec(
                request,
                SpecContext(
                    has_gpu=has_gpu,
                    docker_memory_bytes=await self._docker_memory_bytes(),
                    hf_token=settings.HUGGINGFACE_API_KEY or None,
                ),
            )
            image_name = spec.image

            # 2. Check / Pull Docker Image with streaming progress
            self._update_status(
                state=ServerState.PULLING_IMAGE,
                message=f"Checking Docker image ({image_name})...",
            )
            self._add_log(f"Checking for image {image_name}...")

            def _pull_if_needed() -> bool:
                try:
                    self._client.images.get(image_name)
                    return True
                except (ImageNotFound, NotFound):
                    return False
                except Exception:
                    return False

            has_image = await loop.run_in_executor(None, _pull_if_needed)
            if not has_image:
                self._add_log(f"Image {image_name} not found locally. Initiating pull from Docker Hub...")
                self._update_status(
                    state=ServerState.PULLING_IMAGE,
                    message=f"Pulling {image_name} from Docker Hub...",
                )

                def _stream_pull():
                    for line in self._client.api.pull(image_name, stream=True, decode=True):
                        # A stop or replacement mid-pull must silence this stream;
                        # the download itself is harmless and simply caches layers.
                        if self._superseded(generation):
                            return
                        status_str = line.get("status", "")
                        progress_detail = line.get("progress", "")
                        layer_id = line.get("id", "")
                        if layer_id:
                            msg = f"[{layer_id}] {status_str} {progress_detail}".strip()
                        else:
                            msg = f"{status_str} {progress_detail}".strip()
                        if msg:
                            loop.call_soon_threadsafe(self._add_log, msg)

                await loop.run_in_executor(None, _stream_pull)
                if self._superseded(generation):
                    return
                self._add_log(f"✓ Image {image_name} successfully pulled.")

            # 3. Report what the runtime decided, so its choices are visible in the
            # log rather than implicit in the container's arguments.
            for note in spec.notes:
                self._add_log(note)

            if request.memory_limit_gb or request.cpu_limit:
                limits = []
                if request.memory_limit_gb:
                    limits.append(f"{request.memory_limit_gb:g} GiB of RAM")
                if request.cpu_limit:
                    limits.append(f"{request.cpu_limit:g} CPU cores")
                self._add_log(f"ℹ Container limited to {' and '.join(limits)}.")

            self._update_status(
                state=ServerState.LOADING,
                message=f"Starting container for {request.model_id}...",
            )
            self._add_log(
                f"Starting {self._runtime.key} container for model: {request.model_id}"
            )

            # 4. Start the container
            def _start_container():
                # Forcibly remove any existing container with the same name
                try:
                    old_c = self._client.containers.get(self._runtime.container_name)
                    old_c.remove(force=True)
                except Exception:
                    pass

                if self._superseded(generation):
                    return None

                hf_cache_src = DockerPathResolver.resolve_volume_source(
                    hf_cache_dir,
                    fallback_named_volume="kayak-huggingface-cache",
                )
                volumes = {
                    hf_cache_src: {
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

                raise RuntimeError(
                    f"No free port between {self._runtime.default_port} and "
                    f"{self._runtime.default_port + self.PORT_FALLBACK_ATTEMPTS - 1} to publish "
                    f"the server on. ({last_port_error})"
                )

            container, chosen_port = await loop.run_in_executor(None, _start_container)
            if container is None or chosen_port is None or self._superseded(generation):
                return
            self._status.container_id = container.id
            self._status.port = chosen_port
            self._status.endpoint = self._api_base_for_port(chosen_port)
            self._add_log(f"Container created with ID: {container.id[:12]}")
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

            # 6. Poll health endpoint until healthy (non-blocking)
            await self._poll_health_endpoint(request.model_id, generation, chosen_port)

            # 7. Give the runtime a chance to self-heal a failure it recognises --
            # for vLLM, a model whose default context needs more KV cache than this
            # machine has, which its own error names a working figure for.
            if not self._superseded(generation):
                retry = self._runtime.retry_request(
                    request, self._status, self._log_history
                )
                if retry is not None:
                    self._retry_task = asyncio.create_task(self._redeploy_retry(retry))

        except Exception as error:
            if self._superseded(generation):
                return
            self._add_log(f"Deployment encountered error: {str(error)}")
            self._update_status(
                state=ServerState.ERROR,
                message=f"Failed to deploy {request.model_id}",
                error=str(error),
            )

    async def _redeploy_retry(self, retry: DeployRequest) -> None:
        """Runs a runtime's self-healing retry, reporting what it changed."""
        try:
            await self.deploy_model(retry)
            if retry.max_model_len is not None:
                self._add_log(
                    f"ℹ The model's default context does not fit in this machine's memory; "
                    f"restarted automatically with a {retry.max_model_len}-token context."
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

    async def _get_container(self) -> Optional[Any]:
        """Fetches this runtime's container with fresh attributes, or None if gone."""
        if not self._docker_available or not self._client:
            return None

        def _get() -> Optional[Any]:
            try:
                return self._client.containers.get(self._runtime.container_name)
            except Exception:
                return None

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _get)

    @staticmethod
    def _read_exit(container: Any) -> Tuple[Optional[int], bool]:
        """Returns the container's exit code and whether the kernel OOM-killed it."""
        state = (getattr(container, "attrs", {}) or {}).get("State", {}) or {}
        exit_code = state.get("ExitCode")
        return (
            exit_code if isinstance(exit_code, int) else None,
            bool(state.get("OOMKilled")),
        )

    def _report_container_exit(self, exit_code: Optional[int], oom_killed: bool) -> None:
        """Moves status to ERROR after the container stopped on its own.

        vLLM most often dies because the model did not fit, and it says so on the way
        out. Carrying the exit code and the last few log lines into the status means
        the failure is legible without opening the log drawer.
        """
        if oom_killed:
            # Names the Docker memory limit explicitly. On Docker Desktop that ceiling
            # is the VM's allocation rather than the machine's RAM, so this is the one
            # failure users routinely misread as "but I have plenty of memory".
            detail = (
                "The container was killed for exceeding the memory Docker makes "
                "available, which on Docker Desktop is set independently of the "
                "machine's RAM. Raise it in Docker Desktop under Settings > Resources "
                "> Memory, or try a smaller model, a lower --max-model-len, or a lower "
                "GPU memory fraction."
            )
        elif exit_code:
            # The tail of a crashing vLLM is shutdown noise; the sentence that names
            # the cause is further up.
            reason = extract_failure_reason(self._log_history)
            detail = (
                f"Container exited with code {exit_code}. {reason}"
                if reason
                else f"Container exited with code {exit_code}, with no error in its output."
            )
        else:
            detail = "The container stopped before the server became reachable."

        self._add_log(f"✗ {self._runtime.server_label} container exited (code {exit_code}).")
        self._update_status(
            state=ServerState.ERROR,
            message=f"{self._runtime.server_label} stopped while starting {self._status.model_id or 'the model'}",
            error=detail,
            exit_code=exit_code if exit_code is not None else -1,
        )

    def _endpoint_serves_model(self, payload: Any, model_id: str) -> bool:
        """Whether a health response says the requested model is being served.

        A 200 alone is not proof of readiness: during a backend switch the previous
        server — a Metal process winding down, or an older container — can still be
        answering on the same port, so a deployment used to report READY the moment
        anything at all responded, serving the wrong model. Now that a second server
        runs alongside on a neighbouring port, answering the wrong one is easier
        still, so the runtime decides what counts as its own model.
        """
        return self._runtime.serves_model(payload, model_id)

    async def _poll_health_endpoint(
        self,
        model_id: str,
        generation: Optional[int] = None,
        port: Optional[int] = None,
        timeout_seconds: int = HEALTH_TIMEOUT_SECONDS,
    ) -> None:
        """Waits for the vLLM endpoint to serve this model, or for the container to die.

        This is the sole mechanism for transitioning to READY state. It bounds itself on
        wall-clock time rather than on a number of attempts, and it only accepts an
        answer that names the model being deployed.
        """
        if generation is None:
            generation = self._deploy_generation
        port = port or self._status.port or self._runtime.default_port
        deadline = time.monotonic() + timeout_seconds
        next_container_check = time.monotonic() + CONTAINER_CHECK_INTERVAL_SECONDS

        async with httpx.AsyncClient(timeout=1.0) as client:
            while time.monotonic() < deadline:
                await asyncio.sleep(1.0)

                # Bail if deployment was cancelled, errored, or replaced
                if self._superseded(generation) or self._status.state in (
                    ServerState.ERROR,
                    ServerState.STOPPED,
                    ServerState.IDLE,
                ):
                    return

                for url in self._get_endpoint_urls(port):
                    try:
                        res = await client.get(url)
                        if res.status_code == 200 and self._endpoint_serves_model(
                            res.json(), model_id
                        ):
                            if self._superseded(generation):
                                return
                            self._update_status(
                                state=ServerState.READY,
                                message=f"{self._runtime.server_label} is serving {model_id} on port {port}",
                                exit_code=None,
                            )
                            return
                    except Exception:
                        pass

                # A crashed container will never answer, so waiting out the full
                # deadline would report a timeout for a failure that already happened.
                if time.monotonic() >= next_container_check:
                    next_container_check = time.monotonic() + CONTAINER_CHECK_INTERVAL_SECONDS
                    container = await self._get_container()
                    if self._superseded(generation):
                        return
                    if container is None or container.status not in ("running", "created", "restarting"):
                        exit_code, oom_killed = (
                            self._read_exit(container) if container is not None else (None, False)
                        )
                        self._report_container_exit(exit_code, oom_killed)
                        return

        if self._superseded(generation):
            return
        minutes = timeout_seconds // 60
        self._update_status(
            state=ServerState.ERROR,
            message=f"{self._runtime.server_label} did not become reachable within {minutes} minutes",
            error=(
                "The container is still running but never answered on "
                f"{self._api_base_for_port(port)}. Check the container logs below."
            ),
        )

    async def stop_server(self) -> None:
        """Stops and removes this runtime's container, or its Metal server."""
        async with self._control_lock:
            await self._stop_locked()

    async def _stop_locked(self) -> None:
        # Supersede any in-flight deployment first: its monitors must stop writing
        # status before this method starts rewriting it.
        self._deploy_generation += 1
        self._active_request = None

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
            if metal_status.supported or metal.status_path().exists():
                metal.write_desired(running=False)

        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None

        if self._docker_available and self._client:
            def _stop():
                try:
                    container = self._client.containers.get(self._runtime.container_name)
                    container.stop(timeout=3)
                    container.remove(force=True)
                except Exception:
                    pass

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _stop)

        self._update_status(
            state=ServerState.STOPPED,
            message=f"{self._runtime.server_label} container stopped.",
        )
        self._add_log(f"{self._runtime.server_label} container stopped and removed.")

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

    async def _sync_locked(self) -> DeploymentProgress:
        # 0. Check Metal status first if supported
        metal_status = metal.read_status()
        if self._runtime.supports_metal and metal_status.supported:
            if metal_status.state == "ready" and metal_status.model:
                if self._status.state != ServerState.READY or self._status.model_id != metal_status.model:
                    self._status.state = ServerState.READY
                    self._status.model_id = metal_status.model
                    self._status.message = f"Metal server is healthy and serving {metal_status.model}"
                    self._status.port = metal_status.port or self._runtime.default_port
                    self._status.endpoint = self._runtime.api_base(self._runtime.default_port)
                    self._status.container_id = None
                    self._status.error = None
                    self._broadcast({"type": "status", "data": self._status.model_dump()})
                self._status.logs_tail = self._log_history[-30:]
                return self._status
            elif metal_status.state in ("installing", "starting") and metal_status.model:
                if self._status.state not in (ServerState.STARTING_CONTAINER, ServerState.LOADING):
                    self._status.state = ServerState.LOADING
                    self._status.model_id = metal_status.model
                    self._status.message = f"Metal server is {metal_status.state} for {metal_status.model}..."
                    self._status.port = metal_status.port or self._runtime.default_port
                    self._status.endpoint = self._runtime.api_base(self._runtime.default_port)
                    self._status.container_id = None
                    self._status.error = None
                    self._broadcast({"type": "status", "data": self._status.model_dump()})
                if not self._metal_monitor_task or self._metal_monitor_task.done():
                    # A deployment discovered rather than requested -- the backend
                    # restarted while the launcher was mid-start. Adopt it under a
                    # fresh generation so the new monitor owns the status.
                    self._deploy_generation += 1
                    self._metal_monitor_task = asyncio.create_task(
                        self._run_metal_deployment(
                            metal_status.model, generation=self._deploy_generation
                        )
                    )
                self._status.logs_tail = self._log_history[-30:]
                return self._status
            elif (
                metal_status.state == "error"
                # READY included: a Metal server can die after coming up, and
                # reporting that as a quiet "stopped" hid the launcher's reason.
                and self._status.state
                in (
                    ServerState.LOADING,
                    ServerState.STARTING_CONTAINER,
                    ServerState.READY,
                )
                and self._status.model_id == metal_status.model
            ):
                self._status.state = ServerState.ERROR
                self._status.message = f"Metal deployment failed for {metal_status.model}"
                self._status.error = metal_status.error
                self._broadcast({"type": "status", "data": self._status.model_dump()})
                self._status.logs_tail = self._log_history[-30:]
                return self._status

        # 1. Check Docker container state if docker client is available. The
        # container is read before probing so the probe targets the port the
        # container actually publishes -- after a backend restart it may not be
        # the configured default.
        container = None
        if await self._ensure_docker():
            try:
                loop = asyncio.get_running_loop()
                container = await loop.run_in_executor(
                    None, lambda: self._client.containers.get(self._runtime.container_name)
                )
            except Exception:
                container = None

        active_port = (
            (self._container_host_port(container) if container is not None else None)
            or self._status.port
            or self._runtime.default_port
        )

        # 2. Probe the HTTP endpoint to see if the server is responding
        served_models: List[Dict[str, Any]] = []
        is_endpoint_alive = False
        async with httpx.AsyncClient(timeout=1.5) as client:
            for url in self._get_endpoint_urls(active_port):
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        served_models = resp.json().get("data", [])
                        is_endpoint_alive = True
                        break
                except Exception:
                    continue

        if is_endpoint_alive:
            # Model ID from served models or fallback
            active_model_id = (
                served_models[0].get("id")
                if served_models and served_models[0].get("id")
                else (self._status.model_id or f"{self._runtime.key}-model")
            )

            # If not already marked as READY, update to READY
            if self._status.state != ServerState.READY or self._status.model_id != active_model_id:
                self._status.state = ServerState.READY
                self._status.model_id = active_model_id
                self._status.message = f"{self._runtime.server_label} is healthy and serving {active_model_id}"
                self._status.port = active_port
                self._status.endpoint = self._api_base_for_port(active_port)
                if container:
                    self._status.container_id = container.id
                self._status.error = None
                self._broadcast({"type": "status", "data": self._status.model_dump()})

            if container and container.status == "running":
                self._ensure_log_streamer(container)

        elif container and container.status == "running":
            # Container is running but endpoint is still initializing
            if self._status.state not in (
                ServerState.PULLING_IMAGE,
                ServerState.STARTING_CONTAINER,
                ServerState.LOADING,
            ):
                # Infer model name from container command args if possible
                model_name = self._status.model_id or f"{self._runtime.key}-model"
                cmd = getattr(container, "attrs", {}).get("Config", {}).get("Cmd", [])
                if cmd and "--model" in cmd:
                    try:
                        idx = cmd.index("--model")
                        if idx + 1 < len(cmd):
                            model_name = cmd[idx + 1]
                    except Exception:
                        pass

                self._status.state = ServerState.LOADING
                self._status.model_id = model_name
                self._status.message = f"{self._runtime.server_label} container is running and initializing {model_name}..."
                self._status.port = active_port
                self._status.endpoint = self._api_base_for_port(active_port)
                self._status.container_id = container.id
                self._status.error = None
                self._broadcast({"type": "status", "data": self._status.model_dump()})

            self._ensure_log_streamer(container)

        elif container is not None and self._status.state in _ACTIVE_STATES:
            # The container exists but is no longer running, while we still believe it
            # is deploying or serving. Without this branch a crashed vLLM -- which is
            # what running out of memory looks like -- leaves the UI on an indefinite
            # "initializing" spinner, because the endpoint never answers and the
            # container never reports itself as running again.
            exit_code, oom_killed = self._read_exit(container)
            if exit_code:
                self._report_container_exit(exit_code, oom_killed)
            else:
                # Exit code zero is an orderly shutdown, not a failure.
                self._status.state = ServerState.STOPPED
                self._status.message = f"{self._runtime.server_label} container stopped."
                self._status.container_id = None
                self._broadcast({"type": "status", "data": self._status.model_dump()})

        elif self._status.state == ServerState.READY and not is_endpoint_alive:
            # Server was marked ready previously but is no longer responding
            self._status.state = ServerState.STOPPED
            self._status.message = f"{self._runtime.server_label} container stopped."
            self._status.model_id = None
            self._status.container_id = None
            self._broadcast({"type": "status", "data": self._status.model_dump()})

        self._status.logs_tail = self._log_history[-30:]
        return self._status

    async def _docker_resources(self) -> Tuple[Optional[int], Optional[int]]:
        """Returns (memory bytes, CPU count) as Docker reports them, or Nones.

        On Docker Desktop these are the VM's allocation rather than the laptop's
        hardware, which is exactly what bounds a container.
        """
        if not self._docker_available or not self._client:
            return None, None

        def _read() -> Tuple[Optional[int], Optional[int]]:
            try:
                info = self._client.info()
                total = info.get("MemTotal")
                cpus = info.get("NCPU")
                return (
                    int(total) if total else None,
                    int(cpus) if cpus else None,
                )
            except Exception:
                return None, None

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _read)

    async def _docker_info(self) -> Optional[Dict[str, Any]]:
        """The Docker daemon's own description of itself, or None."""
        if not self._docker_available or not self._client:
            return None

        def _read() -> Optional[Dict[str, Any]]:
            try:
                return self._client.info()
            except Exception:
                return None

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _read)

    async def _gpu_available(self) -> bool:
        """Whether a container started here can be given a GPU.

        Asked of the daemon rather than of this process. Kayak normally runs in a
        container with no NVIDIA tools and no device access, so `nvidia-smi` on its
        own PATH was absent on every machine that ships this way -- and a
        workstation with a GPU quietly got the CPU image. The local binary is still
        consulted, for a Kayak run directly on the host.
        """
        if daemon_offers_gpu(await self._docker_info()):
            return True
        return bool(shutil.which("nvidia-smi"))

    async def _docker_memory_bytes(self) -> Optional[int]:
        """Returns the memory Docker reports for its host, or None if unavailable."""
        memory, _cpus = await self._docker_resources()
        return memory

    async def get_host_capability(self) -> HostCapability:
        """Reports GPU inventory, Docker availability, and whether the image is pulled."""
        image_present: Optional[bool] = None

        if await self._ensure_docker():
            def _has_image() -> Optional[bool]:
                for image_name in self._runtime.candidate_images():
                    try:
                        self._client.images.get(image_name)
                        return True
                    except (ImageNotFound, NotFound):
                        continue
                    except Exception:
                        return None
                return False

            loop = asyncio.get_running_loop()
            image_present = await loop.run_in_executor(None, _has_image)

        total_memory, total_cpus = await self._docker_resources()
        capability = await probe_host_capability(
            self._docker_available, image_present, await self._docker_info()
        )
        capability.total_memory_mb = int(total_memory / (1024 ** 2)) if total_memory else 0
        capability.total_cpus = total_cpus or 0
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
                logger.exception("vLLM watchdog reconcile failed; will retry")

    async def shutdown(self) -> None:
        """Stops background monitors on process shutdown.

        The server container itself is left running deliberately: it survives a
        backend restart and is re-adopted by check_and_sync_status on the way up.
        """
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

