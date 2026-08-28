from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class Modality(str, Enum):
    """What a local server produces.

    One server of each modality may run at a time, independently: a voice model is
    only useful alongside a text model, so starting one must never evict the other.
    """
    TEXT = "text"
    SPEECH = "speech"


class ServerState(str, Enum):
    """Lifecycle states of a local model server's Docker container."""
    IDLE = "idle"
    PULLING_IMAGE = "pulling_image"
    STARTING_CONTAINER = "starting_container"
    LOADING = "loading"
    READY = "ready"
    ERROR = "error"
    STOPPED = "stopped"


class DeployRequest(BaseModel):
    """Request payload for deploying a model via local vLLM."""
    model_id: str = Field(..., description="Hugging Face repository ID or model name (e.g. 'Qwen/Qwen2.5-Coder-7B-Instruct')")
    gpu_memory_utilization: float = Field(0.90, ge=0.1, le=1.0, description="Fraction of GPU memory to reserve for model weights and KV cache")
    max_model_len: Optional[int] = Field(None, ge=256, description="Maximum sequence length (context window)")
    enforce_eager: bool = Field(False, description="Disable CUDA graph capture for reduced memory usage")
    dtype: str = Field("auto", description="Data type for model weights ('auto', 'float16', 'bfloat16')")
    #: CPU deployments only. vLLM refuses to start when this exceeds free memory, so
    #: the default is derived from the memory Docker reports rather than fixed. The
    #: upper bound exists only to reject nonsense; the real ceiling is the machine's
    #: memory, enforced where the request is resolved.
    cpu_kvcache_space_gb: Optional[int] = Field(
        None, ge=1, le=512, description="Memory reserved for the KV cache on CPU, in GiB"
    )
    #: Container-level ceilings, applied by Docker rather than vLLM. None means
    #: unlimited, i.e. everything the Docker VM has -- the right recommendation for
    #: model serving, but the user's call: capping the container protects the rest
    #: of the machine from a hungry model.
    memory_limit_gb: Optional[float] = Field(
        None, ge=1, description="RAM ceiling for the container, in GiB. None = all the memory Docker has."
    )
    cpu_limit: Optional[float] = Field(
        None, gt=0, description="CPU cores the container may use. None = all cores."
    )
    #: A deploy of the model already being served is normally a no-op, so a bare
    #: "make sure it is up" from the chat composer can never restart a server
    #: someone tuned deliberately. Set by the launch dialog, where new settings
    #: are chosen on purpose and applying them requires the restart.
    force_restart: bool = Field(False, description="Restart the server even if this model is already being served")
    # Defaults off: this flag makes vLLM import and execute Python published in the
    # model repository, inside the container, with the Hugging Face token in its
    # environment. It is occasionally required, but it is never a safe default.
    trust_remote_code: bool = Field(False, description="Execute custom modelling code published in the model repository")


class DeploymentProgress(BaseModel):
    """Real-time status and telemetry for one local server."""
    #: Which server this describes. Every status and log event carries it, so a
    #: client watching one stream can tell a voice model's startup from a text
    #: model's without opening a connection per modality.
    modality: Modality = Modality.TEXT
    model_id: Optional[str] = None
    state: ServerState = ServerState.IDLE
    message: str = "The server is not running."
    logs_tail: List[str] = []
    port: int = 8001
    endpoint: str = "http://localhost:8001/v1"
    container_id: Optional[str] = None
    error: Optional[str] = None
    #: Exit code of the container, when it has stopped on its own.
    exit_code: Optional[int] = None


class RuntimeDescriptor(BaseModel):
    """What a client needs to know about a runtime without hardcoding it.

    Served by the API so that the catalogue can filter Hugging Face by the right
    pipeline tags, say which repositories this runtime can actually load, and render
    exactly the settings the runtime honours. A client that hardcoded any of this
    would go stale the moment a backend is added.
    """
    modality: Modality
    key: str
    label: str
    description: str
    #: Hugging Face ``pipeline_tag`` values whose models this runtime serves.
    pipeline_tags: List[str] = []
    #: Hugging Face ``library_name`` values the runtime can load. Empty means the
    #: runtime imposes no library restriction.
    supported_libraries: List[str] = []
    #: Repository-id fragments that identify a supported model when the Hub reports
    #: no library at all, which is the case for some of the most popular ones.
    supported_id_fragments: List[str] = []
    #: Names of DeployRequest fields this runtime honours; the rest are ignored and
    #: must not be offered.
    tunable_fields: List[str] = []


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
    #: CPU cores Docker reports for its host, bounding a container's cpu limit.
    total_cpus: int = 0
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
