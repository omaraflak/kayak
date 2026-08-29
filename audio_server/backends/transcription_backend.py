"""Turning recordings into text, through `transformers`.

Transcription is the well-behaved half of audio: nearly every popular model is a
`transformers` one -- Whisper and its many finetunes above all -- so a single backend
covers the field rather than the dispatch a speech model needs. It is still written
around the pipeline rather than around Whisper, so a different architecture works
without a change here.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

#: Long recordings are transcribed in overlapping windows. Without this, anything
#: past the model's 30-second receptive field is silently dropped -- the request
#: succeeds and returns a transcript that simply stops.
_CHUNK_SECONDS = 30
_OVERLAP_SECONDS = 5


class TranscriptionBackend:
    """Loads one speech-recognition repository and transcribes audio with it."""

    name = "transformers-asr"

    def __init__(self, model_id: str, trust_remote_code: bool = False):
        self.model_id = model_id
        self.trust_remote_code = trust_remote_code
        self._pipeline = None

    def load(self) -> None:
        from transformers import pipeline as hf_pipeline

        self._pipeline = hf_pipeline(
            "automatic-speech-recognition",
            model=self.model_id,
            trust_remote_code=self.trust_remote_code,
            chunk_length_s=_CHUNK_SECONDS,
            stride_length_s=_OVERLAP_SECONDS,
        )

    def transcribe(
        self, audio_path: str, language: Optional[str] = None
    ) -> tuple[str, Optional[str]]:
        """Transcribes one audio file.

        Args:
            audio_path: Path to a decodable audio file.
            language: ISO code to force, or None to let the model decide. Models with
                no multilingual head ignore it rather than failing.

        Returns:
            Tuple of the transcript and the language actually used, if known.
        """
        generate_kwargs = {}
        if language:
            generate_kwargs["language"] = language

        try:
            output = self._pipeline(audio_path, generate_kwargs=generate_kwargs or None)
        except ValueError as error:
            # A monolingual model rejects the language argument outright. Retrying
            # without it transcribes the recording rather than failing the request
            # over an option the user could not have known did not apply.
            if not generate_kwargs:
                raise
            logger.warning(
                "%s did not accept language=%s (%s); transcribing without it",
                self.model_id,
                language,
                error,
            )
            output = self._pipeline(audio_path)
            language = None

        text = (output.get("text") or "").strip()
        return text, language
