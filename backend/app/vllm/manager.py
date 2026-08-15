import asyncio
import re
import time
from pathlib import Path
import shutil
import threading
from typing import Any, Dict, List, Optional, Set, Tuple
import docker
from docker.errors import NotFound, ImageNotFound
import httpx
from backend.app.config import settings
from backend.app.docker_utils import DockerPathResolver
from backend.app.vllm import cache as model_cache
from backend.app.vllm.hardware import probe_host_capability
from backend.app.vllm.models import (
    CachedModel,
    HostCapability,
    ModelCacheInfo,
    VLLMDeployRequest,
    VLLMDeploymentProgress,
    VLLMServerState,
)

GPU_IMAGE = "vllm/vllm-openai:latest"
CPU_IMAGE = "vllm/vllm-openai-cpu:latest"

# A first deployment downloads the image and then tens of gigabytes of weights, so the
# health check has to be patient. Genuine failures are caught by watching the container
# rather than by running out of attempts.
HEALTH_TIMEOUT_SECONDS = 1800
CONTAINER_CHECK_INTERVAL_SECONDS = 5.0

_ACTIVE_STATES = frozenset({
    VLLMServerState.PULLING_IMAGE,
    VLLMServerState.STARTING_CONTAINER,
    VLLMServerState.LOADING,
    VLLMServerState.READY,
})

#: Share of total memory offered to the KV cache. The rest goes to the model weights
#: and the vLLM runtime, which together take far more than the weights alone: loading a
#: 1.4 GiB model on an 7.77 GiB host left only 2.5 GiB free. Subtracting a fixed
#: headroom instead over-promises on exactly the small machines that can least afford it.
_CPU_KVCACHE_MEMORY_SHARE = 0.25
_MIN_CPU_KVCACHE_GIB = 1
_MAX_CPU_KVCACHE_GIB = 8
#: Memory the runtime and the loaded weights need before any is left for the cache.
#: Used to bound an explicit request against what the machine can actually spare.
_CPU_RUNTIME_RESERVE_GIB = 5
#: Used when Docker will not say how much memory it has.
_FALLBACK_CPU_KVCACHE_GIB = 1

#: Lines like "ValueError: Available memory on node 0 ... is less than requested".
_ERROR_LINE = re.compile(r"\b([A-Za-z_]*(?:Error|Exception)):\s*(\S.*)$")

#: Log noise that names an exception type but never explains a failure.
_UNHELPFUL_ERROR_MARKERS = (
    "resource_tracker",
    "FutureWarning",
    "DeprecationWarning",
    "UserWarning",
)


def resolve_cpu_kvcache_gib(
    total_memory_bytes: Optional[int], requested_gib: Optional[int] = None
) -> int:
    """Chooses how much memory to hand vLLM for its CPU KV cache.

    This was previously a hardcoded 12 GiB, which no machine with less than roughly
    14 GiB available could ever satisfy: vLLM refuses to start when the requested KV
    cache exceeds free memory, so the smallest model failed exactly like the largest.

    Args:
        total_memory_bytes: Memory Docker reports for its host, if known.
        requested_gib: An explicit choice from the user, which wins.

    Returns:
        int: Size in GiB, always at least 1.
    """
    if requested_gib:
        # Clamped, not trusted. An oversized request is refused by vLLM minutes into a
        # deployment, long after the weights have downloaded, so it is better caught
        # here than surfaced as a crash.
        ceiling = _MAX_CPU_KVCACHE_GIB
        if total_memory_bytes and total_memory_bytes > 0:
            spare = int(total_memory_bytes / (1024 ** 3)) - _CPU_RUNTIME_RESERVE_GIB
            ceiling = max(_MIN_CPU_KVCACHE_GIB, spare)
        return max(_MIN_CPU_KVCACHE_GIB, min(int(requested_gib), ceiling))

    if not total_memory_bytes or total_memory_bytes <= 0:
        return _FALLBACK_CPU_KVCACHE_GIB

    total_gib = total_memory_bytes / (1024 ** 3)
    share_gib = int(total_gib * _CPU_KVCACHE_MEMORY_SHARE)
    return max(_MIN_CPU_KVCACHE_GIB, min(_MAX_CPU_KVCACHE_GIB, share_gib))


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


class VLLMManager:
    """Manages the lifecycle of a dedicated Docker container running vLLM."""

    CONTAINER_NAME = "kayak-vllm-server"

    def __init__(self):
        self._client: Optional[docker.DockerClient] = None
        self._docker_available: bool = False
        self._status: VLLMDeploymentProgress = VLLMDeploymentProgress()
        self._log_history: List[str] = []
        self._listeners: Set[asyncio.Queue] = set()
        self._monitor_task: Optional[asyncio.Task] = None
        self._log_stop_event: Optional[threading.Event] = None
        self._init_docker()

    def _init_docker(self):
        try:
            self._client = docker.from_env()
            self._client.ping()
            self._docker_available = True
            DockerPathResolver.initialize(self._client)
        except Exception:
            self._client = None
            self._docker_available = False

    def _get_endpoint_urls(self) -> List[str]:
        """Returns the ordered list of vLLM endpoint URLs to probe."""
        return [
            f"http://host.docker.internal:{settings.VLLM_PORT}/v1/models",
            f"{settings.VLLM_API_BASE.rstrip('/')}/models",
            f"http://localhost:{settings.VLLM_PORT}/v1/models",
            f"http://127.0.0.1:{settings.VLLM_PORT}/v1/models",
        ]

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

    def subscribe(self) -> asyncio.Queue:
        """Subscribes to live SSE events from the vLLM manager.

        The greeting must use the same envelope as every later broadcast. It used to
        push the bare status dict, which the client could not read -- so a tab that
        reconnected after an app restart received nothing and kept showing whatever it
        believed before the restart.
        """
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._listeners.add(queue)
        queue.put_nowait({"type": "status", "data": self.get_status().model_dump()})
        return queue

    def unsubscribe(self, queue: asyncio.Queue):
        """Unsubscribes an event queue."""
        self._listeners.discard(queue)

    def _broadcast(self, data: Dict[str, Any]):
        """Dispatches event to all active SSE queues."""
        for queue in list(self._listeners):
            try:
                queue.put_nowait(data)
            except asyncio.QueueFull:
                pass

    def _add_log(self, line: str):
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
        state: VLLMServerState,
        message: str,
        error: Optional[str] = None,
        exit_code: Optional[int] = None,
    ):
        """Updates internal telemetry and broadcasts to frontend."""
        self._status.state = state
        self._status.message = message
        if error is not None:
            self._status.error = error
        if exit_code is not None:
            self._status.exit_code = exit_code

        self._broadcast({"type": "status", "data": self._status.model_dump()})

    def get_status(self) -> VLLMDeploymentProgress:
        """Returns the current deployment and server status."""
        self._status.logs_tail = self._log_history[-30:]
        return self._status

    async def deploy_model(self, request: VLLMDeployRequest) -> VLLMDeploymentProgress:
        """Deploys a Hugging Face model on local vLLM inside a Docker container.

        Args:
            request: Configuration parameters for the vLLM server.

        Returns:
            Current deployment status.
        """
        if not self._docker_available or not self._client:
            self._update_status(
                state=VLLMServerState.ERROR,
                message="Docker is not available on this system.",
                error="Docker daemon unreachable",
            )
            return self.get_status()

        # Guard: if already deploying or serving the same model, skip
        if (
            self._status.model_id == request.model_id
            and self._status.state in _ACTIVE_STATES
        ):
            return self.get_status()

        # Stop existing deployment if running
        await self.stop_server()

        self._log_history.clear()
        self._status = VLLMDeploymentProgress(
            model_id=request.model_id,
            state=VLLMServerState.STARTING_CONTAINER,
            message=f"Preparing environment for {request.model_id}...",
            port=settings.VLLM_PORT,
            endpoint=settings.VLLM_API_BASE,
        )
        self._broadcast({"type": "status", "data": self._status.model_dump()})

        # Ensure HF cache directory on host
        hf_cache_dir = settings.DATA_DIR / "huggingface_cache"
        hf_cache_dir.mkdir(parents=True, exist_ok=True)

        # Start background deployment runner
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()

        self._monitor_task = asyncio.create_task(
            self._run_deployment(request, hf_cache_dir)
        )
        return self.get_status()

    async def _run_deployment(self, request: VLLMDeployRequest, hf_cache_dir: Path):
        """Asynchronous runner that pulls images, starts container, and polls health."""
        try:
            loop = asyncio.get_running_loop()

            # 1. Detect GPU availability upfront to choose the right image
            has_gpu = bool(shutil.which("nvidia-smi"))
            image_name = GPU_IMAGE if has_gpu else CPU_IMAGE

            # 2. Check / Pull Docker Image with streaming progress
            self._update_status(
                state=VLLMServerState.PULLING_IMAGE,
                message=f"Checking vLLM Docker image ({image_name})...",
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
                    state=VLLMServerState.PULLING_IMAGE,
                    message=f"Pulling {image_name} from Docker Hub...",
                )

                def _stream_pull():
                    for line in self._client.api.pull(image_name, stream=True, decode=True):
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
                self._add_log(f"✓ Image {image_name} successfully pulled.")

            # 3. Build Container Execution Arguments & Environment
            env_vars: Dict[str, str] = {
                # HF_HUB_ENABLE_HF_TRANSFER is deprecated and ignored by current
                # huggingface_hub, which warns about it on every start.
                "HF_XET_HIGH_PERFORMANCE": "1",
                "PYTHONUNBUFFERED": "1",
            }
            if settings.HUGGINGFACE_API_KEY:
                env_vars["HF_TOKEN"] = settings.HUGGINGFACE_API_KEY
                env_vars["HUGGING_FACE_HUB_TOKEN"] = settings.HUGGINGFACE_API_KEY

            cmd_args: List[str] = [
                "--model", request.model_id,
                "--port", "8000",
                "--host", "0.0.0.0",
            ]

            device_requests: Optional[List[docker.types.DeviceRequest]] = None
            shm_size: Optional[str] = None

            if has_gpu:
                self._add_log(f"✓ NVIDIA GPU detected. Configuring GPU acceleration for {request.model_id}...")
                cmd_args.extend([
                    "--gpu-memory-utilization", str(request.gpu_memory_utilization),
                    "--dtype", request.dtype,
                ])
                if request.enforce_eager:
                    cmd_args.append("--enforce-eager")
                device_requests = [
                    docker.types.DeviceRequest(count=-1, capabilities=[["gpu"]])
                ]
            else:
                self._add_log(f"ℹ No NVIDIA GPU found. Using CPU image for {request.model_id}...")
                cpu_dtype = "bfloat16" if request.dtype in ("auto", "bfloat16") else request.dtype
                cmd_args.extend([
                    "--dtype", cpu_dtype,
                    "--enforce-eager",
                ])

                # Sized to the machine. vLLM refuses to start when the requested KV
                # cache exceeds free memory, so a fixed figure made every CPU launch
                # fail on any host smaller than that figure.
                total_memory = await self._docker_memory_bytes()
                kvcache_gib = resolve_cpu_kvcache_gib(total_memory, request.cpu_kvcache_space_gb)
                env_vars["VLLM_CPU_KVCACHE_SPACE"] = str(kvcache_gib)
                shm_size = f"{kvcache_gib}g"

                if total_memory:
                    self._add_log(
                        f"ℹ Docker reports {total_memory / 1024 ** 3:.1f} GiB of memory; "
                        f"reserving {kvcache_gib} GiB for the KV cache."
                    )

            if request.trust_remote_code:
                cmd_args.append("--trust-remote-code")
            if request.max_model_len:
                cmd_args.extend(["--max-model-len", str(request.max_model_len)])

            # Enable auto tool calling support for OpenAI-compatible endpoint
            tool_parser = "hermes"
            if "llama" in request.model_id.lower():
                tool_parser = "llama3_json"
            elif "mistral" in request.model_id.lower():
                tool_parser = "mistral"

            cmd_args.extend([
                "--enable-auto-tool-choice",
                "--tool-call-parser", tool_parser,
            ])

            self._update_status(
                state=VLLMServerState.LOADING,
                message=f"Starting container for {request.model_id}...",
            )
            self._add_log(f"Starting vLLM container for model: {request.model_id}")

            # 4. Start the container
            def _start_container():
                # Forcibly remove any existing container with the same name
                try:
                    old_c = self._client.containers.get(self.CONTAINER_NAME)
                    old_c.remove(force=True)
                except Exception:
                    pass

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
                ports = {"8000/tcp": settings.VLLM_PORT}

                run_kwargs: Dict[str, Any] = {
                    "image": image_name,
                    "name": self.CONTAINER_NAME,
                    "command": cmd_args,
                    "detach": True,
                    "volumes": volumes,
                    "ports": ports,
                    "environment": env_vars,
                    "remove": False,
                }

                if device_requests is not None:
                    run_kwargs["device_requests"] = device_requests
                if shm_size is not None:
                    run_kwargs["shm_size"] = shm_size

                return self._client.containers.run(**run_kwargs)

            container = await loop.run_in_executor(None, _start_container)
            self._status.container_id = container.id
            self._add_log(f"Container created with ID: {container.id[:12]}")

            # 5. Spawn background non-blocking daemon thread for container log streaming
            if self._log_stop_event:
                self._log_stop_event.set()
            self._log_stop_event = threading.Event()
            self._spawn_log_stream_thread(container, loop, self._log_stop_event)

            # 6. Poll health endpoint until healthy (non-blocking)
            await self._poll_health_endpoint(request.model_id)

        except Exception as error:
            self._add_log(f"Deployment encountered error: {str(error)}")
            self._update_status(
                state=VLLMServerState.ERROR,
                message=f"Failed to deploy {request.model_id}",
                error=str(error),
            )

    def _spawn_log_stream_thread(
        self,
        container: Any,
        loop: asyncio.AbstractEventLoop,
        stop_event: threading.Event,
    ):
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

        thread = threading.Thread(target=_worker, daemon=True, name="vllm-log-streamer")
        thread.start()

    async def _get_container(self) -> Optional[Any]:
        """Fetches the vLLM container with fresh attributes, or None if it is gone."""
        if not self._docker_available or not self._client:
            return None

        def _get() -> Optional[Any]:
            try:
                return self._client.containers.get(self.CONTAINER_NAME)
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

    def _report_container_exit(self, exit_code: Optional[int], oom_killed: bool):
        """Moves status to ERROR after the container stopped on its own.

        vLLM most often dies because the model did not fit, and it says so on the way
        out. Carrying the exit code and the last few log lines into the status means
        the failure is legible without opening the log drawer.
        """
        if oom_killed:
            detail = (
                "The container was killed for exceeding available memory. Try a smaller "
                "model, a lower --max-model-len, or a lower GPU memory fraction."
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

        self._add_log(f"✗ vLLM container exited (code {exit_code}).")
        self._update_status(
            state=VLLMServerState.ERROR,
            message=f"vLLM stopped while starting {self._status.model_id or 'the model'}",
            error=detail,
            exit_code=exit_code if exit_code is not None else -1,
        )

    async def _poll_health_endpoint(
        self,
        model_id: str,
        timeout_seconds: int = HEALTH_TIMEOUT_SECONDS,
    ):
        """Waits for the vLLM endpoint to answer, or for the container to die trying.

        This is the sole mechanism for transitioning to READY state. It bounds itself on
        wall-clock time rather than on a number of attempts: each attempt probes up to
        five URLs with a one-second timeout apiece, so an attempt budget of 300 could
        represent anything from five to twenty-five minutes.
        """
        urls_to_try = self._get_endpoint_urls() + [
            f"http://host.docker.internal:{settings.VLLM_PORT}/health",
        ]
        deadline = time.monotonic() + timeout_seconds
        next_container_check = time.monotonic() + CONTAINER_CHECK_INTERVAL_SECONDS

        async with httpx.AsyncClient(timeout=1.0) as client:
            while time.monotonic() < deadline:
                await asyncio.sleep(1.0)

                # Bail if deployment was cancelled or errored
                if self._status.state in (
                    VLLMServerState.ERROR,
                    VLLMServerState.STOPPED,
                    VLLMServerState.IDLE,
                ):
                    return

                for url in urls_to_try:
                    try:
                        res = await client.get(url)
                        if res.status_code == 200:
                            self._update_status(
                                state=VLLMServerState.READY,
                                message=f"vLLM is serving {model_id} on port {settings.VLLM_PORT}",
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
                    if container is None or container.status not in ("running", "created", "restarting"):
                        exit_code, oom_killed = (
                            self._read_exit(container) if container is not None else (None, False)
                        )
                        self._report_container_exit(exit_code, oom_killed)
                        return

        minutes = timeout_seconds // 60
        self._update_status(
            state=VLLMServerState.ERROR,
            message=f"vLLM did not become reachable within {minutes} minutes",
            error=(
                "The container is still running but never answered on "
                f"{settings.VLLM_API_BASE}. Check the container logs below."
            ),
        )

    async def stop_server(self):
        """Stops and removes the running vLLM container."""
        if self._log_stop_event:
            self._log_stop_event.set()
            self._log_stop_event = None

        if not self._docker_available or not self._client:
            return

        # Cancel running monitor task first
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None

        def _stop():
            try:
                container = self._client.containers.get(self.CONTAINER_NAME)
                container.stop(timeout=3)
                container.remove(force=True)
            except Exception:
                pass

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _stop)

        self._update_status(
            state=VLLMServerState.STOPPED,
            message="vLLM container stopped.",
        )
        self._add_log("vLLM container stopped and removed.")

    async def check_and_sync_status(self) -> VLLMDeploymentProgress:
        """Inspects Docker and probes the vLLM HTTP endpoint to synchronize internal state."""
        # 1. Probe the HTTP endpoint to see if vLLM is responding
        served_models: List[Dict[str, Any]] = []
        is_endpoint_alive = False
        async with httpx.AsyncClient(timeout=1.5) as client:
            for url in self._get_endpoint_urls():
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        served_models = resp.json().get("data", [])
                        is_endpoint_alive = True
                        break
                except Exception:
                    continue

        # 2. Check Docker container state if docker client is available
        container = None
        if self._docker_available and self._client:
            try:
                loop = asyncio.get_running_loop()
                container = await loop.run_in_executor(
                    None, lambda: self._client.containers.get(self.CONTAINER_NAME)
                )
            except Exception:
                container = None

        if is_endpoint_alive:
            # Model ID from served models or fallback
            active_model_id = (
                served_models[0].get("id")
                if served_models and served_models[0].get("id")
                else (self._status.model_id or "vllm-model")
            )

            # If not already marked as READY, update to READY
            if self._status.state != VLLMServerState.READY or self._status.model_id != active_model_id:
                self._status.state = VLLMServerState.READY
                self._status.model_id = active_model_id
                self._status.message = f"vLLM server is healthy and serving {active_model_id}"
                self._status.port = settings.VLLM_PORT
                self._status.endpoint = settings.VLLM_API_BASE
                if container:
                    self._status.container_id = container.id
                self._status.error = None
                self._broadcast({"type": "status", "data": self._status.model_dump()})

            if container and container.status == "running":
                self._ensure_log_streamer(container)

        elif container and container.status == "running":
            # Container is running but endpoint is still initializing
            if self._status.state not in (
                VLLMServerState.PULLING_IMAGE,
                VLLMServerState.STARTING_CONTAINER,
                VLLMServerState.LOADING,
            ):
                # Infer model name from container command args if possible
                model_name = self._status.model_id or "vllm-model"
                cmd = getattr(container, "attrs", {}).get("Config", {}).get("Cmd", [])
                if cmd and "--model" in cmd:
                    try:
                        idx = cmd.index("--model")
                        if idx + 1 < len(cmd):
                            model_name = cmd[idx + 1]
                    except Exception:
                        pass

                self._status.state = VLLMServerState.LOADING
                self._status.model_id = model_name
                self._status.message = f"vLLM container is running and initializing {model_name}..."
                self._status.port = settings.VLLM_PORT
                self._status.endpoint = settings.VLLM_API_BASE
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
                self._status.state = VLLMServerState.STOPPED
                self._status.message = "vLLM container stopped."
                self._status.container_id = None
                self._broadcast({"type": "status", "data": self._status.model_dump()})

        elif self._status.state == VLLMServerState.READY and not is_endpoint_alive:
            # Server was marked ready previously but is no longer responding
            self._status.state = VLLMServerState.STOPPED
            self._status.message = "vLLM container stopped."
            self._status.model_id = None
            self._status.container_id = None
            self._broadcast({"type": "status", "data": self._status.model_dump()})

        self._status.logs_tail = self._log_history[-30:]
        return self._status

    async def list_served_models(self) -> List[Dict[str, Any]]:
        """Queries the running vLLM OpenAI endpoint for served models."""
        async with httpx.AsyncClient(timeout=2.0) as client:
            for url in self._get_endpoint_urls():
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        models = resp.json().get("data", [])
                        if models:
                            active_id = models[0].get("id")
                            if self._status.state != VLLMServerState.READY or self._status.model_id != active_id:
                                self._status.state = VLLMServerState.READY
                                self._status.model_id = active_id
                                self._status.message = f"vLLM server is healthy and serving {active_id}"
                            return models
                except Exception:
                    continue

        if self._status.model_id and self._status.state == VLLMServerState.READY:
            return [{"id": self._status.model_id, "object": "model", "owned_by": "vllm"}]
        return []

    @property
    def cache_root(self) -> Path:
        """Host directory mounted into the container as the Hugging Face cache."""
        return settings.DATA_DIR / "huggingface_cache"

    async def _docker_memory_bytes(self) -> Optional[int]:
        """Returns the memory Docker reports for its host, or None if unavailable.

        On Docker Desktop this is the VM's allocation rather than the laptop's RAM,
        which is exactly the number that bounds a container.
        """
        if not self._docker_available or not self._client:
            return None

        def _read() -> Optional[int]:
            try:
                total = self._client.info().get("MemTotal")
                return int(total) if total else None
            except Exception:
                return None

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _read)

    async def get_host_capability(self) -> HostCapability:
        """Reports GPU inventory, Docker availability, and whether the image is pulled."""
        image_present: Optional[bool] = None

        if self._docker_available and self._client:
            def _has_image() -> Optional[bool]:
                for image_name in (GPU_IMAGE, CPU_IMAGE):
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

        total_memory = await self._docker_memory_bytes()
        capability = await probe_host_capability(self._docker_available, image_present)
        capability.total_memory_mb = int(total_memory / (1024 ** 2)) if total_memory else 0
        capability.default_cpu_kvcache_gb = resolve_cpu_kvcache_gib(total_memory)
        return capability

    async def get_cache_info(self) -> ModelCacheInfo:
        """Lists locally downloaded model weights and their size on disk."""
        root = self.cache_root
        loop = asyncio.get_running_loop()
        # Walking a weight cache means stat-ing tens of thousands of files.
        models: List[CachedModel] = await loop.run_in_executor(
            None, model_cache.list_cached_models, root
        )
        return ModelCacheInfo(
            path=str(root),
            total_bytes=sum(model.size_bytes for model in models),
            models=models,
        )

    def is_serving(self, repo_id: str) -> bool:
        """Reports whether the given repository is the one currently being served."""
        return bool(
            self._status.model_id == repo_id
            and self._status.state in _ACTIVE_STATES
        )

    async def delete_cached_model(self, repo_id: str) -> int:
        """Removes a repository's weights from the local cache.

        Args:
            repo_id: Hugging Face repository id to evict.

        Returns:
            int: Bytes reclaimed.

        Raises:
            CachePathError: If the id does not name a directory inside the cache.
            FileNotFoundError: If the repository is not cached.
        """
        target = model_cache.resolve_cache_entry(self.cache_root, repo_id)
        if not target.is_dir():
            raise FileNotFoundError(f"'{repo_id}' is not in the local cache.")

        def _remove() -> int:
            freed = model_cache.directory_size_bytes(target)
            shutil.rmtree(target)
            return freed

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _remove)


# Singleton instance
vllm_manager = VLLMManager()
