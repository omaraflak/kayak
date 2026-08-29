"""Speech backends, and how one is chosen for a repository.

There is no equivalent of vLLM for speech: the popular models are split across
mutually incompatible libraries, and every ready-made server is written around one or
two of them. So this image dispatches instead. Each backend declares which
repositories it can load, and the first one that claims a repository serves it.

Adding support for a family of models is a new backend module plus one entry in
``BACKENDS`` -- never a change to the HTTP layer, and never a list of blessed models.
"""

from typing import Tuple

from audio_server.backends.base import SpeechBackend, Voice
from audio_server.backends.kokoro_backend import KokoroBackend
from audio_server.backends.transformers_backend import TransformersBackend

#: Ordered; the first backend that claims a repository serves it. More specific
#: backends come first, so a model with a dedicated implementation is not swallowed
#: by the general one.
BACKENDS: Tuple[type, ...] = (
    KokoroBackend,
    TransformersBackend,
)


class UnsupportedModelError(RuntimeError):
    """Raised when no backend in this image can load a repository."""


def select_backend(model_id: str, trust_remote_code: bool = False) -> SpeechBackend:
    """The backend that will serve ``model_id``.

    Args:
        model_id: Hugging Face repository id.
        trust_remote_code: Whether the backend may execute code from the repository.

    Returns:
        An unloaded backend instance.

    Raises:
        UnsupportedModelError: If nothing in this image can load the repository.
    """
    for backend in BACKENDS:
        if backend.claims(model_id):
            return backend(model_id, trust_remote_code=trust_remote_code)

    supported = ", ".join(backend.name for backend in BACKENDS)
    raise UnsupportedModelError(
        f"No backend in this image can load '{model_id}'. "
        f"Available backends: {supported}."
    )


__all__ = [
    "BACKENDS",
    "SpeechBackend",
    "UnsupportedModelError",
    "Voice",
    "select_backend",
]
