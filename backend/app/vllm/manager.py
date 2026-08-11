import asyncio
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import threading
from typing import Any, Callable, Dict, List, Optional, Set
import docker
from docker.errors import DockerException, NotFound, ImageNotFound
import httpx
from backend.app.config import settings
from backend.app.vllm.models import (
    VLLMDeployRequest,
    VLLMDeploymentProgress,
    VLLMServerState,
)

GPU_IMAGE = "vllm/vllm-openai:latest"
CPU_IMAGE = "vllm/vllm-openai-cpu:latest"

_ACTIVE_STATES = frozenset({
    VLLMServerState.PULLING_IMAGE,
    VLLMServerState.STARTING_CONTAINER,
    VLLMServerState.LOADING,
    VLLMServerState.READY,
})


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
        except Exception:
            self._client = None
            self._docker_available = False

    @property
    def is_docker_available(self) -> bool:
        return self._docker_available

    def subscribe(self) -> asyncio.Queue:
        """Subscribes to live SSE events from the vLLM manager."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._listeners.add(queue)
        queue.put_nowait(self.get_status().model_dump())
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
    ):
        """Updates internal telemetry and broadcasts to frontend."""
        self._status.state = state
        self._status.message = message
        if error is not None:
            self._status.error = error

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
                "HF_HUB_ENABLE_HF_TRANSFER": "1",
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
                env_vars["VLLM_CPU_KVCACHE_SPACE"] = "12"
                shm_size = "12g"

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

                volumes = {
                    str(hf_cache_dir.resolve()): {
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

        thread = threading.Thread(target=_worker, daemon=True, name="vllm-log-streamer")
        thread.start()

    async def _poll_health_endpoint(self, model_id: str, max_attempts: int = 300):
        """Polls vLLM health endpoint until healthy or timeout.

        This is the sole mechanism for transitioning to READY state.
        """
        urls_to_try = [
            f"http://host.docker.internal:{settings.VLLM_PORT}/v1/models",
            f"http://host.docker.internal:{settings.VLLM_PORT}/health",
            f"{settings.VLLM_API_BASE.rstrip('/')}/models",
            f"http://localhost:{settings.VLLM_PORT}/v1/models",
            f"http://127.0.0.1:{settings.VLLM_PORT}/v1/models",
        ]
        async with httpx.AsyncClient(timeout=1.0) as client:
            for _ in range(max_attempts):
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
                            )
                            return
                    except Exception:
                        pass

        # Exhausted all attempts
        self._update_status(
            state=VLLMServerState.ERROR,
            message=f"vLLM server did not become healthy after {max_attempts}s",
            error="Health check timeout",
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


# Singleton instance
vllm_manager = VLLMManager()
