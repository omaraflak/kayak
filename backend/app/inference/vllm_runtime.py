"""Serving text models with vLLM.

Everything here is specific to vLLM: which image to run, how to size a CPU KV cache,
which tool-call parser a model family needs, and the one start failure worth retrying
automatically. The lifecycle around it lives in the manager and is shared with every
other modality.
"""

import re
from typing import List, Optional, Tuple

import docker

from backend.app.config import default_vllm_api_base, settings
from backend.app.inference.models import (
    DeployRequest,
    DeploymentProgress,
    HostCapability,
    Modality,
    ServerState,
)
from backend.app.inference.runtimes import ContainerSpec, Runtime, SpecContext

GPU_IMAGE = "vllm/vllm-openai:latest"
CPU_IMAGE = "vllm/vllm-openai-cpu:latest"

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
#: model id. Parser names must exist in vLLM's registry (vllm/tool_parsers) --
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


class VLLMRuntime(Runtime):
    """Serves any text-generation repository through vLLM's OpenAI-compatible API."""

    modality = Modality.TEXT
    key = "vllm"
    label = "Text generation"
    server_label = "vLLM"
    description = (
        "Chat and completion models served by vLLM, with tool calling enabled."
    )
    container_name = "kayak-vllm-server"
    supports_metal = True
    pipeline_tags = ("text-generation",)
    tunable_fields = (
        "max_model_len",
        "gpu_memory_utilization",
        "cpu_kvcache_space_gb",
        "memory_limit_gb",
        "cpu_limit",
        "dtype",
        "enforce_eager",
        "trust_remote_code",
    )

    @property
    def default_port(self) -> int:
        return settings.VLLM_PORT

    def candidate_images(self) -> Tuple[str, ...]:
        return (GPU_IMAGE, CPU_IMAGE)

    def augment_capability(
        self, capability: HostCapability, docker_memory_bytes: Optional[int]
    ) -> None:
        capability.default_cpu_kvcache_gb = resolve_cpu_kvcache_gib(docker_memory_bytes)

    def api_base(self, port: int) -> str:
        if port == settings.VLLM_PORT:
            # Honours an explicit VLLM_API_BASE override for the configured port.
            return settings.VLLM_API_BASE
        return default_vllm_api_base(port, settings.RUNNING_IN_CONTAINER)

    async def container_spec(
        self, request: DeployRequest, context: SpecContext
    ) -> ContainerSpec:
        spec = ContainerSpec(image=GPU_IMAGE if context.has_gpu else CPU_IMAGE)

        spec.environment.update({
            # HF_HUB_ENABLE_HF_TRANSFER is deprecated and ignored by current
            # huggingface_hub, which warns about it on every start.
            "HF_XET_HIGH_PERFORMANCE": "1",
            "PYTHONUNBUFFERED": "1",
        })
        if context.hf_token:
            spec.environment["HF_TOKEN"] = context.hf_token
            spec.environment["HUGGING_FACE_HUB_TOKEN"] = context.hf_token

        spec.command = [
            "--model", request.model_id,
            "--port", str(self.container_port),
            "--host", "0.0.0.0",
        ]

        if context.has_gpu:
            spec.notes.append(
                f"✓ NVIDIA GPU detected. Configuring GPU acceleration for {request.model_id}..."
            )
            spec.command.extend([
                "--gpu-memory-utilization", str(request.gpu_memory_utilization),
                "--dtype", request.dtype,
            ])
            if request.enforce_eager:
                spec.command.append("--enforce-eager")
            spec.run_kwargs["device_requests"] = [
                docker.types.DeviceRequest(count=-1, capabilities=[["gpu"]])
            ]
        else:
            spec.notes.append(
                f"ℹ No NVIDIA GPU found. Using CPU image for {request.model_id}..."
            )
            cpu_dtype = "bfloat16" if request.dtype in ("auto", "bfloat16") else request.dtype
            spec.command.extend([
                "--dtype", cpu_dtype,
                "--enforce-eager",
            ])

            # Sized to what the container will actually have: the user's
            # allocation when one was chosen, otherwise everything Docker has.
            # vLLM refuses to start when the requested KV cache exceeds free
            # memory, so a fixed figure made every CPU launch fail on any host
            # smaller than that figure.
            effective_memory = context.docker_memory_bytes
            if request.memory_limit_gb:
                limit_bytes = int(request.memory_limit_gb * 1024 ** 3)
                effective_memory = (
                    min(effective_memory, limit_bytes)
                    if effective_memory
                    else limit_bytes
                )
            kvcache_gib = resolve_cpu_kvcache_gib(
                effective_memory, request.cpu_kvcache_space_gb
            )
            spec.environment["VLLM_CPU_KVCACHE_SPACE"] = str(kvcache_gib)
            spec.run_kwargs["shm_size"] = f"{kvcache_gib}g"

            if effective_memory:
                spec.notes.append(
                    f"ℹ The container has {effective_memory / 1024 ** 3:.1f} GiB of memory to work with; "
                    f"reserving {kvcache_gib} GiB for the KV cache."
                )

        if request.trust_remote_code:
            spec.command.append("--trust-remote-code")
        if request.max_model_len:
            spec.command.extend(["--max-model-len", str(request.max_model_len)])

        # Enable auto tool calling support for the OpenAI-compatible endpoint.
        spec.command.extend([
            "--enable-auto-tool-choice",
            "--tool-call-parser", tool_call_parser(request.model_id),
        ])

        return spec

    def retry_request(
        self,
        request: DeployRequest,
        status: DeploymentProgress,
        log_history: List[str],
    ) -> Optional[DeployRequest]:
        """The follow-up request for a start that failed only on context length.

        Only fires when the deployment errored, the user left the context length to
        the model's default, and the log carries vLLM's estimate of what fits. The
        retry sets max_model_len explicitly, so it can never fire twice.
        """
        if status.state != ServerState.ERROR or request.max_model_len is not None:
            return None
        fitted = extract_fitting_context(log_history)
        if fitted is None:
            return None
        return request.model_copy(
            update={"max_model_len": fitted, "force_restart": True}
        )
