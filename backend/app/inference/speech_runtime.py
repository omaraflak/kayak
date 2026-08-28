"""Serving speech-synthesis models.

Unlike text, where vLLM already serves any repository behind one OpenAI-compatible
API, the text-to-speech ecosystem has no such runtime: the popular models are split
across mutually incompatible libraries, and every ready-made server is built around
one or two of them. Kayak therefore runs its own image, which dispatches to a backend
chosen from the repository's Hub metadata. The model is always an argument; nothing
here is written around a particular one.

The image is versioned by its server contract rather than by Kayak's release, so
adding a backend ships an image without requiring a Kayak release, and a Kayak release
does not require an image.
"""

from typing import Tuple

from backend.app.config import default_vllm_api_base, settings
from backend.app.inference.models import DeployRequest, Modality
from backend.app.inference.runtimes import ContainerSpec, Runtime, SpecContext


class SpeechRuntime(Runtime):
    """Serves speech models through an OpenAI-compatible ``/v1/audio/speech``."""

    modality = Modality.SPEECH
    key = "tts"
    label = "Speech synthesis"
    server_label = "The speech server"
    description = (
        "Turns text into audio. Serves any repository one of the runtime's backends "
        "can load."
    )
    container_name = "kayak-tts-server"
    pipeline_tags = ("text-to-speech",)
    #: Backends the image ships with. `transformers` covers the models implemented in
    #: the library itself; Kokoro publishes no library at all on the Hub and is matched
    #: by repository id. Adding a backend is a change here and in the image, never in
    #: a client.
    supported_libraries = ("transformers",)
    supported_id_fragments = ("kokoro",)
    #: Speech models are small and have no KV cache or context window to size, so the
    #: only meaningful knobs are the container's ceilings.
    tunable_fields = (
        "memory_limit_gb",
        "cpu_limit",
        "trust_remote_code",
    )

    @property
    def default_port(self) -> int:
        return settings.TTS_PORT

    def candidate_images(self) -> Tuple[str, ...]:
        return (settings.TTS_IMAGE,)

    def api_base(self, port: int) -> str:
        return default_vllm_api_base(port, settings.RUNNING_IN_CONTAINER)

    async def container_spec(
        self, request: DeployRequest, context: SpecContext
    ) -> ContainerSpec:
        spec = ContainerSpec(image=settings.TTS_IMAGE)

        spec.environment.update({
            "PYTHONUNBUFFERED": "1",
            "HF_XET_HIGH_PERFORMANCE": "1",
        })
        if context.hf_token:
            spec.environment["HF_TOKEN"] = context.hf_token
            spec.environment["HUGGING_FACE_HUB_TOKEN"] = context.hf_token

        spec.command = [
            "--model", request.model_id,
            "--port", str(self.container_port),
            "--host", "0.0.0.0",
        ]
        if request.trust_remote_code:
            spec.command.append("--trust-remote-code")

        spec.notes.append(f"ℹ Loading speech model {request.model_id}...")
        return spec
