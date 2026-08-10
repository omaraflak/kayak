from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class VLLMServerState(str, Enum):
    """Lifecycle states of the local vLLM Docker container."""
    IDLE = "idle"
    PULLING_IMAGE = "pulling_image"
    STARTING_CONTAINER = "starting_container"
    DOWNLOADING_MODEL = "downloading_model"
    INITIALIZING_WEIGHTS = "initializing_weights"
    READY = "ready"
    ERROR = "error"
    STOPPED = "stopped"


class VLLMDeployRequest(BaseModel):
    """Request payload for deploying a model via local vLLM."""
    model_id: str = Field(..., description="Hugging Face repository ID or model name (e.g. 'Qwen/Qwen2.5-Coder-7B-Instruct')")
    gpu_memory_utilization: float = Field(0.90, ge=0.1, le=1.0, description="Fraction of GPU memory to reserve for model weights and KV cache")
    max_model_len: Optional[int] = Field(None, description="Maximum sequence length (context window)")
    enforce_eager: bool = Field(False, description="Disable CUDA graph capture for reduced memory usage")
    dtype: str = Field("auto", description="Data type for model weights ('auto', 'float16', 'bfloat16')")
    trust_remote_code: bool = Field(True, description="Allow custom model architectures from Hugging Face")


class VLLMDeploymentProgress(BaseModel):
    """Real-time status and telemetry for vLLM deployment."""
    model_id: Optional[str] = None
    state: VLLMServerState = VLLMServerState.IDLE
    message: str = "vLLM server is not running."
    progress_percent: Optional[float] = None
    download_speed: Optional[str] = None
    eta: Optional[str] = None
    logs_tail: List[str] = []
    port: int = 8000
    endpoint: str = "http://localhost:8000/v1"
    container_id: Optional[str] = None
    error: Optional[str] = None
