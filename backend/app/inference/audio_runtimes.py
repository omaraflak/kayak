"""Serving audio models: speech synthesis and transcription.

Unlike text, where vLLM already serves any repository behind one OpenAI-compatible
API, the audio ecosystem has no such runtime. For synthesis in particular the popular
models are split across mutually incompatible libraries, and every ready-made server
is built around one or two of them. Kayak therefore runs its own image, which
dispatches to a backend chosen from the repository. The model is always an argument;
nothing here is written around a particular one.

One image serves both directions -- it is told at start which it is -- because they
share torch and `transformers` entirely, and a second image would double a two
gigabyte download to add a flag. They are still two runtimes and therefore two
containers, so transcribing a recording does not require giving up the voice you had
loaded.
"""

from typing import Tuple

from backend.app.config import default_vllm_api_base, settings
from backend.app.inference.models import DeployRequest, Modality
from backend.app.inference.runtimes import ContainerSpec, Runtime, SpecContext


class AudioRuntime(Runtime):
    """Shared behaviour of the two audio runtimes.

    They differ only in which direction they run, which is a command-line flag, and
    in what they are called.
    """

    #: Value passed to the server's --modality flag.
    server_mode: str

    def candidate_images(self) -> Tuple[str, ...]:
        return (settings.AUDIO_IMAGE,)

    def api_base(self, port: int) -> str:
        return default_vllm_api_base(port, settings.RUNNING_IN_CONTAINER)

    async def container_spec(
        self, request: DeployRequest, context: SpecContext
    ) -> ContainerSpec:
        spec = ContainerSpec(image=settings.AUDIO_IMAGE)

        spec.environment.update({
            "PYTHONUNBUFFERED": "1",
            "HF_XET_HIGH_PERFORMANCE": "1",
        })
        if context.hf_token:
            spec.environment["HF_TOKEN"] = context.hf_token
            spec.environment["HUGGING_FACE_HUB_TOKEN"] = context.hf_token

        spec.command = [
            "--modality", self.server_mode,
            "--model", request.model_id,
            "--port", str(self.container_port),
            "--host", "0.0.0.0",
        ]
        if request.trust_remote_code:
            spec.command.append("--trust-remote-code")

        spec.notes.append(f"ℹ Loading {self.label.lower()} model {request.model_id}...")
        return spec


class SpeechRuntime(AudioRuntime):
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
    server_mode = "speech"
    pipeline_tags = ("text-to-speech",)
    default_query = "kokoro"
    #: Backends the image ships with. `transformers` covers the models implemented in
    #: the library itself; Kokoro publishes no library at all on the Hub and is matched
    #: by repository id. Adding a backend is a change here and in the image, never in
    #: a client.
    supported_libraries = ("transformers",)
    supported_id_fragments = ("kokoro",)
    #: Audio models are small and have no KV cache or context window to size, so the
    #: only meaningful knobs are the container's ceilings.
    tunable_fields = (
        "memory_limit_gb",
        "cpu_limit",
        "trust_remote_code",
    )

    @property
    def default_port(self) -> int:
        return settings.TTS_PORT


class TranscriptionRuntime(AudioRuntime):
    """Serves speech-to-text through an OpenAI-compatible ``/v1/audio/transcriptions``."""

    modality = Modality.TRANSCRIPTION
    key = "stt"
    label = "Transcription"
    server_label = "The transcription server"
    description = (
        "Turns recordings into text. Serves any speech-recognition repository "
        "`transformers` implements, Whisper included."
    )
    container_name = "kayak-stt-server"
    server_mode = "transcription"
    pipeline_tags = ("automatic-speech-recognition",)
    default_query = "whisper"
    #: Transcription is the well-behaved half of audio: nearly every popular model is
    #: a `transformers` one, so a single backend covers the field.
    supported_libraries = ("transformers",)
    tunable_fields = (
        "memory_limit_gb",
        "cpu_limit",
        "trust_remote_code",
    )

    @property
    def default_port(self) -> int:
        return settings.STT_PORT
