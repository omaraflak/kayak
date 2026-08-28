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
from backend.app.config import default_vllm_api_base, settings
from backend.app.docker_utils import DockerPathResolver
from backend.app.vllm import cache as model_cache
from backend.app.vllm import metal
from backend.app.vllm.hardware import probe_host_capability
from backend.app.vllm.models import (
    CachedModel,
    HostCapability,
    ModelCacheInfo,
    VLLMDeployRequest,
    VLLMDeploymentProgress,
    VLLMServerState,
)

logger = logging.getLogger(__name__)

GPU_IMAGE = "vllm/vllm-openai:latest"
CPU_IMAGE = "vllm/vllm-openai-cpu:latest"

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
    VLLMServerState.PULLING_IMAGE,
    VLLMServerState.STARTING_CONTAINER,
    VLLMServerState.LOADING,
    VLLMServerState.READY,
})

#: Share of total memory offered to the KV cache. The rest goes to the model weights
#: and the vLLM runtime, which together take far more than the weights alone: loading a
#: 1.4 GiB model on an 7.77 GiB host left only 2.5 GiB free. Subtracting a fixed
#: headroom instead over-promises on exactly the small machines that can least afford it.
#: Deliberately uncapped beyond the share itself: the recommendation scales with the
#: machine, and the user picks the final figure in the launch dialog.
_CPU_KVCACHE_MEMORY_SHARE = 0.25
_MIN_CPU_KVCACHE_GIB = 1
#: Memory the runtime and the loaded weights need before any is left for the cache.
#: Used to bound an explicit request against what the machine can actually spare.
_CPU_RUNTIME_RESERVE_GIB = 5
#: Used when Docker will not say how much memory it has.
_FALLBACK_CPU_KVCACHE_GIB = 1

#: Which vLLM tool-call parser understands each model family's output format.
#: Ordered, first match wins, so more specific names must precede the family
#: catch-all ("deepseek-v3.1" before "deepseek"). Matched against the lowercased
#: model id. Parser names must exist in vLLM's registry (vllm/tool_parsers) —
#: an unknown name stops the server from starting at all.
#:
#: The launcher's Metal path (kayak-launcher, src-tauri/src/metal.rs) carries
#: the same table; the two must agree so a model behaves the same whichever
#: backend serves it.
_TOOL_PARSER_RULES: Tuple[Tuple[str, str], ...] = (
    ("qwen3-coder", "qwen3_coder"),
    ("gpt-oss", "openai"),
    # R1 distills keep their base model's chat template, not DeepSeek's.
    ("deepseek-r1-distill-llama", "llama3_json"),
    ("deepseek-r1-distill", "hermes"),
    ("deepseek-v3.2", "deepseek_v32"),
    ("deepseek-v3.1", "deepseek_v31"),
    ("deepseek-v4", "deepseek_v4"),
    ("deepseek", "deepseek_v3"),
    ("glm-4.7", "glm47"),
    ("glm", "glm45"),
    ("granite-20b-fc", "granite-20b-fc"),
    ("granite-4", "granite4"),
    ("granite", "granite"),
    ("phi-4-mini", "phi4_mini_json"),
    ("phi4-mini", "phi4_mini_json"),
    ("internlm", "internlm"),
    ("kimi-k3", "kimi_k3"),
    ("kimi", "kimi_k2"),
    ("llama-4", "llama4_pythonic"),
    ("llama4", "llama4_pythonic"),
    ("llama", "llama3_json"),
    ("mistral", "mistral"),
    ("jamba", "jamba"),
    ("gemma-4", "gemma4"),
    ("seed-oss", "seed_oss"),
    ("hunyuan", "hunyuan_a13b"),
    ("minimax-m3", "minimax_m3"),
    ("minimax", "minimax_m2"),
    ("ernie", "ernie45"),
    ("olmo-3", "olmo3"),
    ("olmo3", "olmo3"),
)


def tool_call_parser(model_id: str) -> str:
    """The vLLM tool-call parser for a model, by family.

    Falls back to "hermes", which matches the format Qwen and most other
    open models emit. A mismatched parser does not crash the server; it just
    leaves tool calls unrecognised in the plain text of the reply.
    """
    lowered = model_id.lower()
    for needle, parser in _TOOL_PARSER_RULES:
        if needle in lowered:
            return parser
    return "hermes"


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

#: vLLM's own advice when a model's default context does not fit in the KV cache:
#: "... the estimated maximum model length is 9344. Try increasing ..."
_FITTING_CONTEXT_PATTERN = re.compile(r"estimated maximum model length is (\d+)")

#: Below this, a model cannot hold a useful conversation, so an automatic retry
#: with a shrunken context would only produce a server nobody can use.
_MIN_USEFUL_CONTEXT = 1024


def extract_fitting_context(log_lines: List[str]) -> Optional[int]:
    """Reads the context length vLLM says would fit, from a failed start's output.

    vLLM refuses to start when the model's maximum sequence length needs more KV
    cache than the machine has, but its error names the length that would fit.
    That number feeds an automatic retry instead of being shown to the user as
    homework.

    Args:
        log_lines: Captured container output, oldest first.

    Returns:
        Optional[int]: The usable context length, or None if the failure was
        something else or the fitting length is too small to be worth serving.
    """
    for line in reversed(log_lines):
        match = _FITTING_CONTEXT_PATTERN.search(line)
        if match:
            value = int(match.group(1))
            return value if value >= _MIN_USEFUL_CONTEXT else None
    return None


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
        # Clamped to what the machine can spare, not trusted outright: an oversized
        # request is refused by vLLM minutes into a deployment, long after the
        # weights have downloaded, so it is better caught here than as a crash.
        # With no memory figure to clamp against, the user's number stands.
        if total_memory_bytes and total_memory_bytes > 0:
            spare = int(total_memory_bytes / (1024 ** 3)) - _CPU_RUNTIME_RESERVE_GIB
            ceiling = max(_MIN_CPU_KVCACHE_GIB, spare)
            return max(_MIN_CPU_KVCACHE_GIB, min(int(requested_gib), ceiling))
        return max(_MIN_CPU_KVCACHE_GIB, int(requested_gib))

    if not total_memory_bytes or total_memory_bytes <= 0:
        return _FALLBACK_CPU_KVCACHE_GIB

    total_gib = total_memory_bytes / (1024 ** 3)
    share_gib = int(total_gib * _CPU_KVCACHE_MEMORY_SHARE)
    return max(_MIN_CPU_KVCACHE_GIB, share_gib)


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
        self._init_state()
        self._init_docker()

    def _init_state(self) -> None:
        """Initializes in-memory state, shared with tests that skip Docker probing."""
        self._client: Optional[docker.DockerClient] = None
        self._docker_available: bool = False
        self._status: VLLMDeploymentProgress = VLLMDeploymentProgress()
        self._log_history: List[str] = []
        self._listeners: Set[asyncio.Queue] = set()
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
        self._active_request: Optional[VLLMDeployRequest] = None

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

    @staticmethod
    def _api_base_for_port(port: int) -> str:
        """The OpenAI-compatible base URL for a server published on `port`."""
        if port == settings.VLLM_PORT:
            # Honours an explicit VLLM_API_BASE override for the configured port.
            return settings.VLLM_API_BASE
        return default_vllm_api_base(port, settings.RUNNING_IN_CONTAINER)

    def _get_endpoint_urls(self, port: Optional[int] = None) -> List[str]:
        """Returns the ordered list of vLLM endpoint URLs to probe.

        Probes the port the current deployment actually publishes, which may
        differ from the configured default when that port was taken.
        """
        port = port or self._status.port or settings.VLLM_PORT
        urls = [
            f"http://host.docker.internal:{port}/v1/models",
            f"{self._api_base_for_port(port).rstrip('/')}/models",
            f"http://localhost:{port}/v1/models",
            f"http://127.0.0.1:{port}/v1/models",
        ]
        # Deduplicated, order preserved: the api-base entry usually repeats one
        # of the fixed hosts.
        return list(dict.fromkeys(urls))

    @staticmethod
    def _container_host_port(container: Any) -> Optional[int]:
        """The host port a discovered container actually publishes, if readable."""
        try:
            ports = container.attrs["NetworkSettings"]["Ports"]["8000/tcp"]
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

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        """Unsubscribes an event queue."""
        self._listeners.discard(queue)

    def _broadcast(self, data: Dict[str, Any]) -> None:
        """Dispatches event to all active SSE queues.

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
        state: VLLMServerState,
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

    def get_status(self) -> VLLMDeploymentProgress:
        """Returns the current deployment and server status."""
        self._status.logs_tail = self._log_history[-30:]
        return self._status

    async def deploy_model(self, request: VLLMDeployRequest) -> VLLMDeploymentProgress:
        """Deploys a Hugging Face model on local vLLM or Apple GPU Metal via launcher.

        Serialized with stop and status-sync: overlapping deploys and stops used to
        interleave freely, so two rapid clicks could race two deployment runners
        that removed each other's containers.

        Args:
            request: Configuration parameters for the vLLM server.

        Returns:
            Current deployment status.
        """
        async with self._control_lock:
            return await self._deploy_locked(request)

    async def _deploy_locked(self, request: VLLMDeployRequest) -> VLLMDeploymentProgress:
        # 1. Handle MLX models on Apple Silicon
        if metal.is_mlx_model(request.model_id):
            metal_status = metal.read_status()
            if not metal_status.supported:
                self._update_status(
                    state=VLLMServerState.ERROR,
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
            self._status = VLLMDeploymentProgress(
                model_id=request.model_id,
                state=VLLMServerState.STARTING_CONTAINER,
                message=f"Starting Metal server for {request.model_id}...",
                port=metal_status.port or settings.VLLM_PORT,
                endpoint=settings.VLLM_API_BASE,
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
        if (metal_status.supported and metal_status.state != "stopped") or (
            not metal_status.supported and metal.status_path().exists()
        ):
            metal.write_desired(running=False)
            if self._metal_monitor_task and not self._metal_monitor_task.done():
                self._metal_monitor_task.cancel()

        if not await self._ensure_docker():
            self._update_status(
                state=VLLMServerState.ERROR,
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
        self._status = VLLMDeploymentProgress(
            model_id=request.model_id,
            state=VLLMServerState.STARTING_CONTAINER,
            message=f"Preparing environment for {request.model_id}...",
            port=settings.VLLM_PORT,
            endpoint=settings.VLLM_API_BASE,
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
                    VLLMServerState.STOPPED,
                    VLLMServerState.IDLE,
                ):
                    return

                if not answers_this_deployment(metal_status):
                    continue

                if metal_status.state == "ready":
                    self._status.port = metal_status.port or settings.VLLM_PORT
                    self._status.endpoint = settings.VLLM_API_BASE
                    self._update_status(
                        state=VLLMServerState.READY,
                        message=f"Metal server is healthy and serving {model_id}",
                    )
                    self._add_log(f"✓ Metal server is ready and serving {model_id}.")
                    return
                elif metal_status.state == "installing":
                    msg = f"Installing Metal environment for {model_id}..."
                    if self._status.message != msg:
                        self._update_status(state=VLLMServerState.LOADING, message=msg)
                        self._add_log(f"Metal status: {msg}")
                elif metal_status.state == "starting":
                    msg = f"Starting Metal server for {model_id}..."
                    if self._status.message != msg:
                        self._update_status(state=VLLMServerState.LOADING, message=msg)
                        self._add_log(f"Metal status: {msg}")
                elif metal_status.state == "error":
                    err_msg = metal_status.error or "Metal server encountered an error."
                    self._update_status(
                        state=VLLMServerState.ERROR,
                        message=f"Failed to start Metal server for {model_id}",
                        error=err_msg,
                    )
                    self._add_log(f"✗ Metal server error: {err_msg}")
                    return

            if not self._superseded(generation):
                self._update_status(
                    state=VLLMServerState.ERROR,
                    message=f"Metal server did not become ready within {timeout_seconds // 60} minutes",
                    error="Timed out waiting for launcher to start Metal server.",
                )
        except asyncio.CancelledError:
            pass

    async def _run_deployment(
        self, request: VLLMDeployRequest, hf_cache_dir: Path, generation: int
    ) -> None:
        """Asynchronous runner that pulls images, starts container, and polls health.

        The generation captured at launch gates every write to shared state: once a
        newer deploy or stop supersedes this runner, it must fall silent instead of
        overwriting the state of whatever replaced it.
        """
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

                # Sized to what the container will actually have: the user's
                # allocation when one was chosen, otherwise everything Docker has.
                # vLLM refuses to start when the requested KV cache exceeds free
                # memory, so a fixed figure made every CPU launch fail on any host
                # smaller than that figure.
                total_memory = await self._docker_memory_bytes()
                effective_memory = total_memory
                if request.memory_limit_gb:
                    limit_bytes = int(request.memory_limit_gb * 1024 ** 3)
                    effective_memory = (
                        min(total_memory, limit_bytes) if total_memory else limit_bytes
                    )
                kvcache_gib = resolve_cpu_kvcache_gib(effective_memory, request.cpu_kvcache_space_gb)
                env_vars["VLLM_CPU_KVCACHE_SPACE"] = str(kvcache_gib)
                shm_size = f"{kvcache_gib}g"

                if effective_memory:
                    self._add_log(
                        f"ℹ The container has {effective_memory / 1024 ** 3:.1f} GiB of memory to work with; "
                        f"reserving {kvcache_gib} GiB for the KV cache."
                    )

            if request.trust_remote_code:
                cmd_args.append("--trust-remote-code")
            if request.max_model_len:
                cmd_args.extend(["--max-model-len", str(request.max_model_len)])

            # Enable auto tool calling support for OpenAI-compatible endpoint
            cmd_args.extend([
                "--enable-auto-tool-choice",
                "--tool-call-parser", tool_call_parser(request.model_id),
            ])

            if request.memory_limit_gb or request.cpu_limit:
                limits = []
                if request.memory_limit_gb:
                    limits.append(f"{request.memory_limit_gb:g} GiB of RAM")
                if request.cpu_limit:
                    limits.append(f"{request.cpu_limit:g} CPU cores")
                self._add_log(f"ℹ Container limited to {' and '.join(limits)}.")

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
                    "name": self.CONTAINER_NAME,
                    "command": cmd_args,
                    "detach": True,
                    "volumes": volumes,
                    "environment": env_vars,
                    "remove": False,
                }

                if device_requests is not None:
                    run_kwargs["device_requests"] = device_requests
                if shm_size is not None:
                    run_kwargs["shm_size"] = shm_size
                # Container-level ceilings, chosen by the user. Unset means the
                # container may use everything the Docker VM has.
                if request.memory_limit_gb:
                    run_kwargs["mem_limit"] = f"{int(request.memory_limit_gb * 1024)}m"
                if request.cpu_limit:
                    run_kwargs["nano_cpus"] = int(request.cpu_limit * 1_000_000_000)

                # The configured port first, then neighbours. On a launcher
                # install Kayak itself is published on the default vLLM port, so
                # binding it fails with "port is already allocated" -- which used
                # to end the deployment instead of trying the port next door.
                last_port_error: Optional[Exception] = None
                for offset in range(self.PORT_FALLBACK_ATTEMPTS):
                    port = settings.VLLM_PORT + offset
                    run_kwargs["ports"] = {"8000/tcp": port}
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
                            self._client.containers.get(self.CONTAINER_NAME).remove(force=True)
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
                    f"No free port between {settings.VLLM_PORT} and "
                    f"{settings.VLLM_PORT + self.PORT_FALLBACK_ATTEMPTS - 1} to publish "
                    f"the server on. ({last_port_error})"
                )

            container, chosen_port = await loop.run_in_executor(None, _start_container)
            if container is None or chosen_port is None or self._superseded(generation):
                return
            self._status.container_id = container.id
            self._status.port = chosen_port
            self._status.endpoint = self._api_base_for_port(chosen_port)
            self._add_log(f"Container created with ID: {container.id[:12]}")
            if chosen_port != settings.VLLM_PORT:
                self._add_log(
                    f"ℹ Serving on port {chosen_port} because {settings.VLLM_PORT} is in use."
                )

            # 5. Spawn background non-blocking daemon thread for container log streaming
            if self._log_stop_event:
                self._log_stop_event.set()
            self._log_stop_event = threading.Event()
            self._spawn_log_stream_thread(container, loop, self._log_stop_event)

            # 6. Poll health endpoint until healthy (non-blocking)
            await self._poll_health_endpoint(request.model_id, generation, chosen_port)

            # 7. Self-heal the one predictable zero-config failure: the model's
            # default context needs more KV cache than this machine has. vLLM's
            # error names the context length that would fit, so retry once with
            # it rather than handing the user a number to type back in.
            if not self._superseded(generation):
                retry = self._context_retry_request(request)
                if retry is not None:
                    self._retry_task = asyncio.create_task(self._redeploy_with_context(retry))

        except Exception as error:
            if self._superseded(generation):
                return
            self._add_log(f"Deployment encountered error: {str(error)}")
            self._update_status(
                state=VLLMServerState.ERROR,
                message=f"Failed to deploy {request.model_id}",
                error=str(error),
            )

    def _context_retry_request(self, request: VLLMDeployRequest) -> Optional[VLLMDeployRequest]:
        """The follow-up request for a start that failed only on context length.

        Only fires when the deployment errored, the user left the context length to
        the model's default, and the log carries vLLM's estimate of what fits. The
        retry sets max_model_len explicitly, so it can never fire twice.
        """
        if self._status.state != VLLMServerState.ERROR or request.max_model_len is not None:
            return None
        fitted = extract_fitting_context(self._log_history)
        if fitted is None:
            return None
        return request.model_copy(update={"max_model_len": fitted, "force_restart": True})

    async def _redeploy_with_context(self, retry: VLLMDeployRequest) -> None:
        try:
            await self.deploy_model(retry)
            self._add_log(
                f"ℹ The model's default context does not fit in this machine's memory; "
                f"restarted automatically with a {retry.max_model_len}-token context."
            )
        except Exception:
            logger.exception("Automatic context-length retry failed to start")

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

        self._add_log(f"✗ vLLM container exited (code {exit_code}).")
        self._update_status(
            state=VLLMServerState.ERROR,
            message=f"vLLM stopped while starting {self._status.model_id or 'the model'}",
            error=detail,
            exit_code=exit_code if exit_code is not None else -1,
        )

    @staticmethod
    def _endpoint_serves_model(payload: Any, model_id: str) -> bool:
        """Whether a /v1/models response says the requested model is being served.

        A 200 alone is not proof of readiness: during a backend switch the previous
        server — a Metal process winding down, or an older container — can still be
        answering on the same port, so a deployment used to report READY the moment
        anything at all responded, serving the wrong model.
        """
        try:
            served = payload.get("data", [])
        except AttributeError:
            return False
        return any(
            isinstance(entry, dict) and entry.get("id") == model_id for entry in served
        )

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
        port = port or self._status.port or settings.VLLM_PORT
        deadline = time.monotonic() + timeout_seconds
        next_container_check = time.monotonic() + CONTAINER_CHECK_INTERVAL_SECONDS

        async with httpx.AsyncClient(timeout=1.0) as client:
            while time.monotonic() < deadline:
                await asyncio.sleep(1.0)

                # Bail if deployment was cancelled, errored, or replaced
                if self._superseded(generation) or self._status.state in (
                    VLLMServerState.ERROR,
                    VLLMServerState.STOPPED,
                    VLLMServerState.IDLE,
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
                                state=VLLMServerState.READY,
                                message=f"vLLM is serving {model_id} on port {port}",
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
            state=VLLMServerState.ERROR,
            message=f"vLLM did not become reachable within {minutes} minutes",
            error=(
                "The container is still running but never answered on "
                f"{self._api_base_for_port(port)}. Check the container logs below."
            ),
        )

    async def stop_server(self) -> None:
        """Stops and removes the running vLLM container or Metal server."""
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

    async def _sync_locked(self) -> VLLMDeploymentProgress:
        # 0. Check Metal status first if supported
        metal_status = metal.read_status()
        if metal_status.supported:
            if metal_status.state == "ready" and metal_status.model:
                if self._status.state != VLLMServerState.READY or self._status.model_id != metal_status.model:
                    self._status.state = VLLMServerState.READY
                    self._status.model_id = metal_status.model
                    self._status.message = f"Metal server is healthy and serving {metal_status.model}"
                    self._status.port = metal_status.port or settings.VLLM_PORT
                    self._status.endpoint = settings.VLLM_API_BASE
                    self._status.container_id = None
                    self._status.error = None
                    self._broadcast({"type": "status", "data": self._status.model_dump()})
                self._status.logs_tail = self._log_history[-30:]
                return self._status
            elif metal_status.state in ("installing", "starting") and metal_status.model:
                if self._status.state not in (VLLMServerState.STARTING_CONTAINER, VLLMServerState.LOADING):
                    self._status.state = VLLMServerState.LOADING
                    self._status.model_id = metal_status.model
                    self._status.message = f"Metal server is {metal_status.state} for {metal_status.model}..."
                    self._status.port = metal_status.port or settings.VLLM_PORT
                    self._status.endpoint = settings.VLLM_API_BASE
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
                    VLLMServerState.LOADING,
                    VLLMServerState.STARTING_CONTAINER,
                    VLLMServerState.READY,
                )
                and self._status.model_id == metal_status.model
            ):
                self._status.state = VLLMServerState.ERROR
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
                    None, lambda: self._client.containers.get(self.CONTAINER_NAME)
                )
            except Exception:
                container = None

        active_port = (
            (self._container_host_port(container) if container is not None else None)
            or self._status.port
            or settings.VLLM_PORT
        )

        # 2. Probe the HTTP endpoint to see if vLLM is responding
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
                else (self._status.model_id or "vllm-model")
            )

            # If not already marked as READY, update to READY
            if self._status.state != VLLMServerState.READY or self._status.model_id != active_model_id:
                self._status.state = VLLMServerState.READY
                self._status.model_id = active_model_id
                self._status.message = f"vLLM server is healthy and serving {active_model_id}"
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

    @property
    def cache_root(self) -> Path:
        """Host directory mounted into the container as the Hugging Face cache."""
        return settings.DATA_DIR / "huggingface_cache"

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

    async def _docker_memory_bytes(self) -> Optional[int]:
        """Returns the memory Docker reports for its host, or None if unavailable."""
        memory, _cpus = await self._docker_resources()
        return memory

    async def get_host_capability(self) -> HostCapability:
        """Reports GPU inventory, Docker availability, and whether the image is pulled."""
        image_present: Optional[bool] = None

        if await self._ensure_docker():
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

        total_memory, total_cpus = await self._docker_resources()
        capability = await probe_host_capability(self._docker_available, image_present)
        capability.total_memory_mb = int(total_memory / (1024 ** 2)) if total_memory else 0
        capability.total_cpus = total_cpus or 0
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
