"""Any speech model implemented in `transformers`.

This is the general backend: it claims every repository, so it sits last and serves
whatever no specialised backend took. That covers a large family -- VITS/MMS across
roughly a thousand languages, SpeechT5, Bark, MeloTTS, Parler, VibeVoice, Orpheus and
others -- through one loader, which is why it is worth having even though it cannot
load the single most popular model.

A repository `transformers` does not implement fails at load, and the failure appears
in the deployment log with the library's own explanation. That is the honest outcome:
the alternative is a curated list of blessed models, which is exactly what this image
exists to avoid.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from audio_server.backends.base import SpeechBackend, Voice

logger = logging.getLogger(__name__)

#: Models whose forward pass takes a speaker embedding rather than a named voice.
#: The embedding set is a published dataset, so the voices are discovered from it.
_SPEAKER_EMBEDDING_DATASET = "Matthijs/cmu-arctic-xvectors"


class TransformersBackend(SpeechBackend):
    name = "transformers"

    @classmethod
    def claims(cls, model_id: str) -> bool:
        # Last in the chain, so claiming everything means "try transformers", not
        # "transformers can definitely load this".
        return True

    def __init__(self, model_id: str, trust_remote_code: bool = False):
        super().__init__(model_id, trust_remote_code)
        self._pipeline = None
        self._speaker_embeddings = None
        self._voices: List[Voice] = []

    def load(self) -> None:
        from transformers import pipeline as hf_pipeline

        self._pipeline = hf_pipeline(
            "text-to-speech",
            model=self.model_id,
            trust_remote_code=self.trust_remote_code,
        )

        if self._needs_speaker_embedding():
            self._load_speaker_embeddings()

    def _needs_speaker_embedding(self) -> bool:
        """Whether this model's generation takes a speaker embedding."""
        model = getattr(self._pipeline, "model", None)
        config = getattr(model, "config", None)
        return getattr(config, "model_type", "") == "speecht5"

    def _load_speaker_embeddings(self) -> None:
        try:
            from datasets import load_dataset

            dataset = load_dataset(_SPEAKER_EMBEDDING_DATASET, split="validation")
        except Exception:
            logger.exception(
                "%s wants a speaker embedding but the embedding set could not be "
                "loaded; synthesis will use the model's default",
                self.model_id,
            )
            return

        self._speaker_embeddings = dataset
        # A handful of evenly spaced speakers rather than all 7931, which is a list
        # no one can choose from. Indices are stable for a versioned dataset.
        step = max(1, len(dataset) // 8)
        self._voices = [
            Voice(id=str(index), label=f"Speaker {position + 1}")
            for position, index in enumerate(range(0, len(dataset), step))
        ][:8]

    def voices(self) -> List[Voice]:
        return list(self._voices)

    def synthesize(
        self, text: str, voice: Optional[str], speed: float
    ) -> Tuple[np.ndarray, int]:
        # Imported here rather than at module scope so that the dispatch table can
        # be inspected wherever the speech stack is not installed.
        import numpy as np

        forward_params = {}
        if self._speaker_embeddings is not None:
            import torch

            index = int(voice) if voice and voice.isdigit() else 0
            index = min(index, len(self._speaker_embeddings) - 1)
            forward_params["speaker_embeddings"] = torch.tensor(
                self._speaker_embeddings[index]["xvector"]
            ).unsqueeze(0)

        output = self._pipeline(text, forward_params=forward_params or None)

        audio = np.asarray(output["audio"], dtype=np.float32).squeeze()
        sample_rate = int(output["sampling_rate"])

        if speed and speed != 1.0:
            audio, sample_rate = _resample_for_speed(audio, sample_rate, speed)

        return audio, sample_rate


def _resample_for_speed(
    audio: np.ndarray, sample_rate: int, speed: float
) -> Tuple[np.ndarray, int]:
    """Changes playback rate for models with no speed control of their own.

    Done by resampling rather than by asking the model, because most of these models
    have no speed parameter. It shifts pitch slightly, which is the honest trade for
    supporting the control uniformly across every backend.
    """
    import numpy as np

    if audio.size == 0:
        return audio, sample_rate
    target_length = max(1, int(round(audio.size / speed)))
    positions = np.linspace(0, audio.size - 1, target_length)
    return np.interp(positions, np.arange(audio.size), audio).astype(np.float32), sample_rate
