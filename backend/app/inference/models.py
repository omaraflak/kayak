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
    TRANSCRIPTION = "transcription"


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
    """Request payload for starting a local model server."""
    model_id: str = Field(..., description="Hugging Face repository ID or model name (e.g. 'Qwen/Qwen2.5-Coder-7B-Instruct')")
    max_model_len: Optional[int] = Field(None, ge=256, description="Maximum sequence length (context window)")
    dtype: str = Field("auto", description="Data type for model weights ('auto', 'bfloat16', 'float32')")
    #: vLLM refuses to start when this exceeds free memory, so the default is
    #: derived from the memory Docker reports rather than fixed. The upper bound
    #: exists only to reject nonsense; the real ceiling is the machine's memory,
    #: enforced where the request is resolved.
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
    #: Which vLLM tool-call parser to use, when the one chosen from the model's
    #: family must be overridden. Set by the automatic retry after vLLM refuses a
    #: parser; left empty, the runtime picks one from the model id.
    tool_parser: Optional[str] = Field(None, description="Override the tool-call parser chosen for this model")


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
    #: Set when an ERROR describes a model that had been serving and then stopped,
    #: rather than one that never started. The two need different words: "couldn't
    #: start" for a model that answered questions a minute ago reads as a lie.
    crashed: bool = False


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
    #: Query the catalogue opens with for this runtime, so switching to it shows
    #: something rather than an empty result. A suggestion, not a restriction --
    #: the search box accepts anything.
    default_query: str = ""


class HostCapability(BaseModel):
    """What Docker Desktop can give a model container on this Mac.

    Containers on a Mac run on the CPU: Docker Desktop has no GPU passthrough. The
    Apple GPU is reached through the launcher instead, reported by MetalStatus.
    """
    docker_available: bool
    #: Memory Docker Desktop's VM has, which is what actually bounds a container --
    #: it is set independently of the Mac's own RAM.
    total_memory_mb: int = 0
    #: CPU cores Docker Desktop's VM has, bounding a container's cpu limit.
    total_cpus: int = 0
    #: KV cache size a deployment would use by default, in GiB.
    default_cpu_kvcache_gb: int = 0
    #: Whether the runtime's image is already pulled, when it could be determined.
    image_present: Optional[bool] = None


class MetalStatus(BaseModel):
    """Metal inference, as reported by the desktop launcher.

    Everything defaults to unavailable so that a Kayak running without a
    launcher -- from docker-compose, or under a launcher too old to know about
    Metal -- reports "not supported" rather than offering something nothing can
    service.
    """
    #: False unless the launcher is present and the Mac is Apple Silicon.
    supported: bool = False
    #: Whether the vllm-metal environment has been installed on the host.
    installed: bool = False
    #: stopped, installing, starting, ready, or error.
    state: str = "stopped"
    model: Optional[str] = None
    port: int = 0
    error: Optional[str] = None
    #: The raw output behind `error`, for the model's Details drawer. Newer
    #: launchers keep it apart so that `error` can be a sentence for a person.
    error_detail: Optional[str] = None
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
    #: Whether the launcher keeps raw output apart from `error`, which makes
    #: `error` a sentence written for a person, shown as it is. Older launchers
    #: never write the key and put raw output in `error`.
    separates_error_detail: bool = False


class ModelClassification(BaseModel):
    """Which local runtime should serve a repository.

    Answered from the Hub's own metadata rather than guessed from the name, because
    the name is not reliable: pressing Start on a cached Kokoro sent it to vLLM,
    which spent minutes on a model it could never load.
    """
    repo_id: str
    #: The runtime that claims it, or None when nothing here can serve it.
    modality: Optional[Modality] = None
    runtime_key: Optional[str] = None
    #: What the Hub says, echoed so the UI can explain an unsupported model.
    pipeline_tag: Optional[str] = None
    library_name: Optional[str] = None
    #: False when the repository was found but no runtime can load it.
    supported: bool = False
    #: Set when the Hub could not be reached, so the caller can fall back rather
    #: than treat "unknown" as "unsupported".
    unknown: bool = False
    #: The makers ask people to accept terms before downloading it.
    gated: bool = False
    #: Hugging Face has no such repository -- or none this Mac may see.
    not_found: bool = False
    #: Downloading it needs a Hugging Face token that is missing or was refused.
    needs_token: bool = False
    #: Why it cannot be started as things stand, written for the person asking.
    #: Set whenever the start would only fail after a long download.
    message: Optional[str] = None


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
