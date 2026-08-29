"""Kokoro, through the `kokoro` package.

Kokoro is the most downloaded speech model on the Hub by a wide margin, and it
publishes no ``library_name`` at all -- so it is matched by repository id. It also
does not load through `transformers`, which is precisely why this image dispatches
rather than assuming one loader.

Nothing here is written around the 82M checkpoint specifically: any repository laid
out the way Kokoro's is -- a `voices/` directory of speaker tensors -- loads through
this backend, including community forks and future versions.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from audio_server.backends.base import SpeechBackend, Voice

logger = logging.getLogger(__name__)

#: Kokoro names its voice files by a language/gender prefix: "af_heart" is American
#: English, female. The mapping is Kokoro's own convention, not a list of voices --
#: voices themselves are discovered from the repository.
_LANGUAGE_PREFIXES = {
    "a": ("en-us", "American English"),
    "b": ("en-gb", "British English"),
    "e": ("es", "Spanish"),
    "f": ("fr-fr", "French"),
    "h": ("hi", "Hindi"),
    "i": ("it", "Italian"),
    "j": ("ja", "Japanese"),
    "p": ("pt-br", "Brazilian Portuguese"),
    "z": ("zh", "Mandarin Chinese"),
}

_DEFAULT_LANGUAGE = ("en-us", "American English")

#: Voice preferred when the repository ships one by that name. Kokoro's voices sort
#: alphabetically, which puts "af_alloy" first -- nobody's deliberate choice. Matched
#: on the name after the language/gender prefix, so it works for whichever prefix a
#: repository happens to publish it under, and falls back when it ships none.
_PREFERRED_VOICE_NAME = "michael"


def voice_name(voice_id: str) -> str:
    """The speaker name in a Kokoro voice id: "am_michael" -> "michael"."""
    return voice_id.split("_", 1)[-1].lower()


def preferred_voice(voice_ids: List[str]) -> Optional[str]:
    """The default voice for a set of Kokoro voices.

    Args:
        voice_ids: Voice ids the repository publishes, in listing order.

    Returns:
        The preferred voice when present, otherwise the first one, or None when the
        repository publishes no voices at all.
    """
    for voice_id in voice_ids:
        if voice_name(voice_id) == _PREFERRED_VOICE_NAME:
            return voice_id
    return voice_ids[0] if voice_ids else None


def voice_language(voice_id: str) -> Tuple[str, str]:
    """The language tag and label a Kokoro voice id implies."""
    return _LANGUAGE_PREFIXES.get(voice_id[:1], _DEFAULT_LANGUAGE)


def voice_label(voice_id: str) -> str:
    """A readable name for a voice id such as ``af_heart``."""
    _tag, language = voice_language(voice_id)
    name = voice_name(voice_id).replace("_", " ").title()
    return f"{name} ({language})"


class KokoroBackend(SpeechBackend):
    name = "kokoro"

    @classmethod
    def claims(cls, model_id: str) -> bool:
        return "kokoro" in model_id.lower()

    def __init__(self, model_id: str, trust_remote_code: bool = False):
        super().__init__(model_id, trust_remote_code)
        self._pipelines: dict = {}
        self._voices: List[Voice] = []

    def load(self) -> None:
        from huggingface_hub import list_repo_files

        # Voices are discovered from the repository rather than hardcoded, so a fork
        # that ships different speakers is served correctly without a code change.
        try:
            files = list_repo_files(self.model_id)
        except Exception:
            logger.exception("Could not list %s; falling back to no voice list", self.model_id)
            files = []

        voice_ids = sorted(
            name[len("voices/"):-len(".pt")]
            for name in files
            if name.startswith("voices/") and name.endswith(".pt")
        )
        self._voices = [
            Voice(id=voice_id, label=voice_label(voice_id), language=voice_language(voice_id)[0])
            for voice_id in voice_ids
        ]

        # Loading one pipeline eagerly turns a broken install into a startup failure,
        # which the deployment log shows, rather than a failure on first synthesis.
        self._pipeline_for(self.default_voice())

    def default_voice(self) -> Optional[str]:
        return preferred_voice([voice.id for voice in self._voices])

    def _pipeline_for(self, voice: Optional[str]):
        """One pipeline per language code; Kokoro's G2P is language-specific."""
        lang_code = (voice or "a")[:1]
        if lang_code not in self._pipelines:
            from kokoro import KPipeline

            self._pipelines[lang_code] = KPipeline(
                lang_code=lang_code, repo_id=self.model_id
            )
        return self._pipelines[lang_code]

    def voices(self) -> List[Voice]:
        return list(self._voices)

    def synthesize(
        self, text: str, voice: Optional[str], speed: float
    ) -> Tuple[np.ndarray, int]:
        # Imported here rather than at module scope so that the dispatch table can
        # be inspected wherever the speech stack is not installed.
        import numpy as np

        chosen = voice or self.default_voice()
        pipeline = self._pipeline_for(chosen)

        segments = [
            np.asarray(result.audio, dtype=np.float32)
            for result in pipeline(text, voice=chosen, speed=speed)
            if result.audio is not None
        ]
        if not segments:
            return np.zeros(0, dtype=np.float32), 24000
        return np.concatenate(segments), 24000
