import asyncio
from datetime import datetime
import json
import os
from pathlib import Path
import re
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


class VLLMManager:
    """Manages the lifecycle of a dedicated Docker container running vLLM."""

    CONTAINER_NAME = "kayak-vllm-server"
    DEFAULT_IMAGE = "vllm/vllm-openai:latest"

    def __init__(self):
        self._client: Optional[docker.DockerClient] = None
        self._docker_available: bool = False
        self._status: VLLMDeploymentProgress = VLLMDeploymentProgress()
        self._log_history: List[str] = []
        self._listeners: Set[asyncio.Queue] = set()
        self._monitor_task: Optional[asyncio.Task] = None
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
        # Immediately push current status
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
        progress: Optional[float] = None,
        speed: Optional[str] = None,
        eta: Optional[str] = None,
        error: Optional[str] = None,
    ):
        """Updates internal telemetry and broadcasts to frontend."""
        self._status.state = state
        self._status.message = message
        if progress is not None:
            self._status.progress_percent = progress
        if speed is not None:
            self._status.download_speed = speed
        if eta is not None:
            self._status.eta = eta
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
            VLLMDeploymentProgress: Current deployment status.
        """
        if not self._docker_available or not self._client:
            self._update_status(
                state=VLLMServerState.ERROR,
                message="Docker is not available on this system.",
                error="Docker daemon unreachable",
            )
            return self.get_status()

        # Stop existing deployment if running
        await self.stop_server()

        self._log_history.clear()
        self._status = VLLMDeploymentProgress(
            model_id=request.model_id,
            state=VLLMServerState.STARTING_CONTAINER,
            message=f"Preparing environment for {request.model_id}...",
            port=8000,
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
        """Asynchronous runner that pulls images, starts container, and streams logs."""
        try:
            loop = asyncio.get_running_loop()

            # 1. Check / Pull Docker Image with streaming progress
            image_name = self.DEFAULT_IMAGE
            self._update_status(
                state=VLLMServerState.PULLING_IMAGE,
                message=f"Checking vLLM Docker image ({image_name})...",
            )
            self._add_log(f"Checking for image {image_name}...")

            def _pull_if_needed():
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
                    progress=10.0,
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
                            self._add_log(msg)

                await loop.run_in_executor(None, _stream_pull)
                self._add_log(f"✓ Image {image_name} successfully pulled.")

            # 2. Build Container Command Arguments
            cmd_args = [
                "--model", request.model_id,
                "--port", "8000",
                "--host", "0.0.0.0",
                "--gpu-memory-utilization", str(request.gpu_memory_utilization),
                "--dtype", request.dtype,
            ]
            if request.trust_remote_code:
                cmd_args.append("--trust-remote-code")
            if request.enforce_eager:
                cmd_args.append("--enforce-eager")
            if request.max_model_len:
                cmd_args.extend(["--max-model-len", str(request.max_model_len)])

            # 3. Environment Variables
            env_vars: Dict[str, str] = {
                "HF_HUB_ENABLE_HF_TRANSFER": "1",
            }
            if settings.HUGGINGFACE_API_KEY:
                env_vars["HF_TOKEN"] = settings.HUGGINGFACE_API_KEY
                env_vars["HUGGING_FACE_HUB_TOKEN"] = settings.HUGGINGFACE_API_KEY

            # 4. Detect GPU Device Requests
            device_requests = []
            try:
                # Test if GPU support exists in Docker daemon
                device_requests = [
                    docker.types.DeviceRequest(count=-1, capabilities=[["gpu"]])
                ]
            except Exception:
                device_requests = []

            self._update_status(
                state=VLLMServerState.DOWNLOADING_MODEL,
                message=f"Launching container and downloading weights for {request.model_id}...",
            )
            self._add_log(f"Starting vLLM container for model: {request.model_id}")

            def _start_container():
                # Forcibly remove any existing container with the same name if present
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
                ports = {"8000/tcp": 8000}
                
                try:
                    container = self._client.containers.run(
                        image=image_name,
                        name=self.CONTAINER_NAME,
                        command=cmd_args,
                        detach=True,
                        volumes=volumes,
                        ports=ports,
                        environment=env_vars,
                        device_requests=device_requests if device_requests else None,
                        remove=False,
                    )
                    return container
                except Exception as e:
                    # Fallback without device requests (CPU mode or standard)
                    self._add_log(f"Standard GPU run failed ({e}), attempting standard execution...")
                    try:
                        old_c = self._client.containers.get(self.CONTAINER_NAME)
                        old_c.remove(force=True)
                    except Exception:
                        pass
                    return self._client.containers.run(
                        image=image_name,
                        name=self.CONTAINER_NAME,
                        command=cmd_args,
                        detach=True,
                        volumes=volumes,
                        ports=ports,
                        environment=env_vars,
                        remove=False,
                    )

            container = await loop.run_in_executor(None, _start_container)
            self._status.container_id = container.id
            self._add_log(f"Container created with ID: {container.id[:12]}")

            # 5. Stream and Parse Logs
            await self._monitor_container_logs(container, request.model_id)

        except Exception as error:
            self._add_log(f"Deployment encountered error: {str(error)}")
            self._update_status(
                state=VLLMServerState.ERROR,
                message=f"Failed to deploy {request.model_id}",
                error=str(error),
            )

    async def _monitor_container_logs(self, container: Any, model_id: str):
        """Streams container logs and parses download percentages and status."""
        loop = asyncio.get_running_loop()

        def _log_generator():
            try:
                for line in container.logs(stream=True, follow=True):
                    yield line.decode("utf-8", errors="replace")
            except Exception:
                return

        # Start health check polling in parallel
        health_check_task = asyncio.create_task(self._poll_health_endpoint())

        try:
            for log_line in _log_generator():
                clean = log_line.strip()
                if not clean:
                    continue

                self._add_log(clean)

                # Parse download progress: e.g. "Fetching 12 files: 45%" or "Downloading (...) 67%"
                if "%" in clean and ("Download" in clean or "Fetching" in clean or "Loading" in clean):
                    percent_match = re.search(r"(\d+(?:\.\d+)?)%", clean)
                    if percent_match:
                        pct = float(percent_match.group(1))
                        self._update_status(
                            state=VLLMServerState.DOWNLOADING_MODEL,
                            message=f"Downloading model files ({pct:.0f}%)...",
                            progress=pct,
                        )

                # Parse initialization
                if "Loading model weights" in clean or "Initializing KV cache" in clean or "Capturing CUDA graph" in clean:
                    self._update_status(
                        state=VLLMServerState.INITIALIZING_WEIGHTS,
                        message="Allocating PagedAttention & initializing weights...",
                        progress=100.0,
                    )

                # Parse readiness
                if "Uvicorn running on" in clean or "Application startup complete" in clean:
                    self._update_status(
                        state=VLLMServerState.READY,
                        message=f"vLLM is serving {model_id} on port 8000!",
                        progress=100.0,
                    )
                    break

                # Parse errors
                if "OutOfMemoryError" in clean or "CUDA out of memory" in clean:
                    self._update_status(
                        state=VLLMServerState.ERROR,
                        message="CUDA Out of Memory: try lowering gpu_memory_utilization or max_model_len",
                        error="CUDA Out of Memory",
                    )
                    break

                await asyncio.sleep(0.01)

        finally:
            if not health_check_task.done():
                await health_check_task

    async def _poll_health_endpoint(self, max_attempts: int = 120):
        """Polls vLLM health endpoint until healthy or container stops."""
        async with httpx.AsyncClient(timeout=1.0) as client:
            for _ in range(max_attempts):
                await asyncio.sleep(2.0)
                if self._status.state == VLLMServerState.ERROR:
                    return
                try:
                    res = await client.get("http://localhost:8000/v1/models")
                    if res.status_code == 200:
                        self._update_status(
                            state=VLLMServerState.READY,
                            message=f"vLLM server is healthy and responding to requests!",
                            progress=100.0,
                        )
                        return
                except Exception:
                    pass

    async def stop_server(self):
        """Stops and removes the running vLLM container."""
        if not self._docker_available or not self._client:
            return

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
