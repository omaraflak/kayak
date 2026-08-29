"""Turning recordings into text, through `transformers`.

Transcription is the well-behaved half of audio: nearly every popular model is a
`transformers` one -- Whisper and its many finetunes above all -- so a single backend
covers the field rather than the dispatch a speech model needs. It is still written
around the pipeline rather than around Whisper, so a different architecture works
without a change here.
"""

from __future__ import annotations

import logging
import math
from typing import Callable, Optional, Tuple

logger = logging.getLogger(__name__)

#: Long recordings are transcribed in overlapping windows. Without this, anything
#: past the model's 30-second receptive field is silently dropped -- the request
#: succeeds and returns a transcript that simply stops.
_CHUNK_SECONDS = 30
_OVERLAP_SECONDS = 5


def _progress_pipeline_class():
    """A pipeline that reports each window as the model finishes it.

    Subclassed rather than patched onto an instance: `_forward` is where the model
    runs on a window, so overriding it is the documented place to observe that,
    and the hook then travels with the pipeline instead of being installed and
    removed around every call.
    """
    from transformers.pipelines.automatic_speech_recognition import (
        AutomaticSpeechRecognitionPipeline,
    )

    class ProgressReportingPipeline(AutomaticSpeechRecognitionPipeline):
        #: Called each time a forward pass finishes. Set per request.
        progress_hook = None

        def _forward(self, model_inputs, **kwargs):
            # Reported after the call returns, so the count is work finished
            # rather than work started.
            result = super()._forward(model_inputs, **kwargs)
            if self.progress_hook is not None:
                self.progress_hook()
            return result

    return ProgressReportingPipeline


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
            pipeline_class=_progress_pipeline_class(),
        )

    def _expected_forwards(self, audio) -> int:
        """How many forward passes the pipeline will make over this recording.

        Asks the pipeline to window the audio and counts what it yields, rather
        than recomputing the window arithmetic here. Duplicating that arithmetic
        was a second copy of a rule the library owns -- a change to how it chunks,
        or a model that windows differently, and the denominator would quietly
        drift while the numerator stayed right.

        The windows are then grouped into batches, and it is the batches that
        become forward passes, which is the unit progress is counted in.

        Costs one extra pass of feature extraction: measured at roughly 1% of the
        transcription it is measuring, which is worth an exact number.
        """
        windows = sum(
            1 for _ in self._pipeline.preprocess(audio, **self._pipeline._preprocess_params)
        )
        batch_size = max(1, int(getattr(self._pipeline, "_batch_size", 1) or 1))
        return max(1, math.ceil(windows / batch_size))

    def transcribe(
        self,
        audio_path: str,
        language: Optional[str] = None,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> tuple[str, Optional[str]]:
        """Transcribes one audio file.

        Args:
            audio_path: Path to a decodable audio file.
            language: ISO code to force, or None to let the model decide. Models with
                no multilingual head ignore it rather than failing.
            on_progress: Called with (windows done, windows total) as the model
                works through the recording.

        Returns:
            Tuple of the transcript and the language actually used, if known.
        """
        from transformers.pipelines.audio_utils import ffmpeg_read

        # Decoded here rather than inside the pipeline so the length is known before
        # the work starts. The pipeline would otherwise decode it itself, and there
        # would be no denominator to report progress against.
        with open(audio_path, "rb") as handle:
            audio = ffmpeg_read(
                handle.read(), self._pipeline.feature_extractor.sampling_rate
            )

        total = self._expected_forwards(audio)
        if on_progress:
            on_progress(0, total)

        # The pipeline reports each window as it finishes; nothing about how the
        # audio is chunked or transcribed is reimplemented here, so the transcript
        # is exactly what it would have been without the hook.
        done = 0

        def advance() -> None:
            nonlocal done
            done += 1
            if on_progress:
                on_progress(min(done, total), total)

        self._pipeline.progress_hook = advance
        try:
            return self._run(audio, language)
        finally:
            self._pipeline.progress_hook = None

    def _run(self, audio, language: Optional[str]) -> tuple[str, Optional[str]]:
        generate_kwargs = {}
        if language:
            generate_kwargs["language"] = language

        try:
            output = self._pipeline(audio, generate_kwargs=generate_kwargs or None)
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
            output = self._pipeline(audio)
            language = None

        text = (output.get("text") or "").strip()
        return text, language
