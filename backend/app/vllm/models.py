from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class VLLMServerState(str, Enum):
    """Lifecycle states of the local vLLM Docker container."""
    IDLE = "idle"
    PULLING_IMAGE = "pulling_image"
    STARTING_CONTAINER = "starting_container"
    LOADING = "loading"
    READY = "ready"
    ERROR = "error"
    STOPPED = "stopped"


class VLLMDeployRequest(BaseModel):
    """Request payload for deploying a model via local vLLM."""
    model_id: str = Field(..., description="Hugging Face repository ID or model name (e.g. 'Qwen/Qwen2.5-Coder-7B-Instruct')")
    gpu_memory_utilization: float = Field(0.90, ge=0.1, le=1.0, description="Fraction of GPU memory to reserve for model weights and KV cache")
    max_model_len: Optional[int] = Field(None, ge=256, description="Maximum sequence length (context window)")
    enforce_eager: bool = Field(False, description="Disable CUDA graph capture for reduced memory usage")
    dtype: str = Field("auto", description="Data type for model weights ('auto', 'float16', 'bfloat16')")
    #: CPU deployments only. vLLM refuses to start when this exceeds free memory, so
    #: the default is derived from the memory Docker reports rather than fixed.
    cpu_kvcache_space_gb: Optional[int] = Field(
        None, ge=1, le=64, description="Memory reserved for the KV cache on CPU, in GiB"
    )
    # Defaults off: this flag makes vLLM import and execute Python published in the
    # model repository, inside the container, with the Hugging Face token in its
    # environment. It is occasionally required, but it is never a safe default.
    trust_remote_code: bool = Field(False, description="Execute custom modelling code published in the model repository")


class VLLMDeploymentProgress(BaseModel):
    """Real-time status and telemetry for vLLM deployment."""
    model_id: Optional[str] = None
    state: VLLMServerState = VLLMServerState.IDLE
    message: str = "vLLM server is not running."
    logs_tail: List[str] = []
    port: int = 8001
    endpoint: str = "http://localhost:8001/v1"
    container_id: Optional[str] = None
    error: Optional[str] = None
    #: Exit code of the container, when it has stopped on its own.
    exit_code: Optional[int] = None


class GPUDevice(BaseModel):
    """A single accelerator visible to the host."""
    name: str
    total_memory_mb: int


class HostCapability(BaseModel):
    """What this machine can serve locally."""
    docker_available: bool
    gpus: List[GPUDevice] = []
    total_vram_mb: int = 0
    #: Memory Docker reports for its host. On Docker Desktop this is the VM's
    #: allocation, which is what actually bounds a CPU deployment.
    total_memory_mb: int = 0
    #: KV cache size a CPU deployment would use by default, in GiB.
    default_cpu_kvcache_gb: int = 0
    #: 'cuda' when the GPU image will be used, 'cpu' otherwise.
    accelerator: str = "cpu"
    #: Whether the vLLM image is already pulled, when it could be determined.
    image_present: Optional[bool] = None


class MetalStatus(BaseModel):
    """Metal inference, as reported by the desktop launcher.

    Everything defaults to unavailable so that a Kayak running without a
    launcher -- from docker-compose, or under a launcher too old to know about
    Metal -- reports "not supported" rather than offering something nothing can
    service.
    """
    #: False unless the launcher is present and the machine is Apple Silicon.
    supported: bool = False
    #: Whether the vllm-metal environment has been installed on the host.
    installed: bool = False
    #: stopped, installing, starting, ready, or error.
    state: str = "stopped"
    model: Optional[str] = None
    port: int = 0
    error: Optional[str] = None
    #: Why Metal is unavailable when the hardware could otherwise do it, such as
    #: an Intel build of the launcher running under Rosetta. Distinct from
    #: `error`, which describes a start that was attempted and failed.
    detail: Optional[str] = None
    #: The start-request token this status answers, echoed by the launcher.
    #: A "ready" carrying a token other than the one just written describes
    #: the previous server, not the one being started.
    request: Optional[str] = None
    #: Whether the launcher is new enough to echo request tokens at all.
    #: Older launchers never write the key, and their statuses are matched by
    #: model id instead.
    acknowledges_requests: bool = False


class MetalStartRequest(BaseModel):
    """Asks the launcher to serve a model on the host GPU."""
    model_id: str = Field(..., description="MLX repository, e.g. 'mlx-community/Qwen3.8-27B-8bit'")


class CachedModel(BaseModel):
    """A model repository whose weights are already on this machine."""
    repo_id: str
    size_bytes: int
    #: Directory mtime as a POSIX timestamp; a rough "last touched".
    modified_at: float


class ModelCacheInfo(BaseModel):
    """The local weight cache, and where it lives."""
    path: str
    total_bytes: int
    models: List[CachedModel] = []
