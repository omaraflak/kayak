"""An OpenAI-compatible audio server that serves whatever model it is given.

Started as ``python -m audio_server --modality speech|transcription --model <repo_id>``,
the same way the vLLM image is started with a text model. The model is an argument; the
image is a runtime. One process serves one model in one direction -- the two directions
share this image only because they share torch and `transformers`, not because they
share a container.

Endpoints follow OpenAI's audio API, so Kayak talks to audio the way it talks to text
and anything else speaking that API can use this too:

    GET  /v1/models                 the loaded model, which is how readiness is verified
    GET  /v1/audio/voices           what this model offers, discovered rather than listed
    POST /v1/audio/speech           text in, audio out
    POST /v1/audio/transcriptions   audio in, text out
"""

import argparse
import asyncio
import io
import logging
import os
import tempfile
from typing import List, Optional

import numpy as np
import soundfile as sf
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from audio_server.backends import SpeechBackend, UnsupportedModelError, select_backend
from audio_server.backends.transcription_backend import TranscriptionBackend
from audio_server.chunking import split_for_synthesis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("audio_server")

#: Formats soundfile writes reliably in this image. MP3 is deliberately absent: it
#: depends on the libsndfile build, and returning a WAV labelled as an MP3 would be
#: worse than saying it is unavailable.
SUPPORTED_FORMATS = {
    "wav": ("WAV", "audio/wav"),
    "flac": ("FLAC", "audio/flac"),
    "ogg": ("OGG", "audio/ogg"),
}

#: Silence inserted between chunks so sentences do not run together.
_GAP_SECONDS = 0.12


class SpeechRequest(BaseModel):
    """OpenAI's ``POST /v1/audio/speech`` body."""
    input: str = Field(..., description="Text to speak")
    #: Accepted and ignored when it names the loaded model, for API compatibility:
    #: this server holds one model, chosen when the container started.
    model: Optional[str] = None
    voice: Optional[str] = None
    response_format: str = "wav"
    speed: float = Field(1.0, gt=0.25, le=4.0)


class VoiceInfo(BaseModel):
    id: str
    label: str
    language: Optional[str] = None


class VoiceListResponse(BaseModel):
    """Voices, and which one the server uses when the caller names none.

    The default travels with the list so a client opens on the same voice the
    server would have chosen, rather than on whichever sorts first.
    """
    voices: List[VoiceInfo] = []
    default_voice: Optional[str] = None


class TranscriptionResponse(BaseModel):
    """OpenAI's ``POST /v1/audio/transcriptions`` JSON response."""
    text: str
    language: Optional[str] = None


class WorkProgress(BaseModel):
    """How far the work currently in flight has got, in chunks.

    Reported rather than only logged: a long recording or a long article is
    minutes of silence otherwise, and scraping a log line for a number is not a
    contract. The unit is a chunk either way -- a sentence group being spoken, or
    a window of audio being transcribed.
    """
    #: True while work is running. False between requests.
    active: bool = False
    chunks_done: int = 0
    chunks_total: int = 0


class ServerState:
    """The single loaded model, and the lock serialising work against it."""

    def __init__(self) -> None:
        self.model_id: str = ""
        self.modality: str = "speech"
        self.backend: Optional[SpeechBackend] = None
        self.progress = WorkProgress()
        # Most of these models are not safe to call concurrently, and a second
        # request arriving mid-generation corrupts the first one's output rather
        # than queueing behind it.
        self.lock = asyncio.Lock()

    def require(self, modality: str):
        """The loaded backend, or a refusal explaining what this server is.

        A transcription request reaching a speech server is a wiring mistake, and
        saying so beats whatever a synthesis model would do with an audio file.
        """
        if not self.backend:
            raise HTTPException(status_code=503, detail="The model is still loading.")
        if self.modality != modality:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"This server does {self.modality}, not {modality}. "
                    f"It is serving {self.model_id}."
                ),
            )
        return self.backend


state = ServerState()
app = FastAPI(title="Kayak audio server")


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok" if state.backend else "loading",
        "model": state.model_id,
        "modality": state.modality,
    }


@app.get("/v1/audio/progress", response_model=WorkProgress)
async def work_progress() -> WorkProgress:
    """Where the work in flight has got to, by chunk."""
    return state.progress


@app.get("/v1/models")
async def list_models() -> dict:
    """The loaded model, in OpenAI's shape.

    Kayak verifies readiness by checking that this names the model it asked for --
    a 200 alone is not proof, since another server may be answering on the port.
    """
    if not state.backend:
        raise HTTPException(status_code=503, detail="The model is still loading.")
    return {
        "object": "list",
        "data": [
            {
                "id": state.model_id,
                "object": "model",
                "owned_by": state.backend.name,
            }
        ],
    }


@app.get("/v1/audio/voices", response_model=VoiceListResponse)
async def list_voices() -> VoiceListResponse:
    """Voices the loaded model offers.

    Served rather than hardcoded anywhere: models differ completely here, and a
    client with its own list would be wrong for every model but one.
    """
    backend = state.require("speech")
    return VoiceListResponse(
        voices=[
            VoiceInfo(id=voice.id, label=voice.label, language=voice.language)
            for voice in backend.voices()
        ],
        default_voice=backend.default_voice(),
    )


@app.post("/v1/audio/speech")
async def create_speech(request: SpeechRequest) -> Response:
    """Speaks the given text and returns an audio file."""
    state.require("speech")

    text_format = request.response_format.lower()
    if text_format not in SUPPORTED_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"'{request.response_format}' is not available in this server. "
                f"Supported formats: {', '.join(sorted(SUPPORTED_FORMATS))}."
            ),
        )

    chunks = split_for_synthesis(request.input)
    if not chunks:
        raise HTTPException(status_code=400, detail="There is no text to speak.")

    async with state.lock:
        state.progress = WorkProgress(
            active=True, chunks_done=0, chunks_total=len(chunks)
        )
        try:
            audio, sample_rate = await asyncio.get_running_loop().run_in_executor(
                None, _synthesize_all, chunks, request.voice, request.speed
            )
        except Exception as error:
            logger.exception("Synthesis failed for %s", state.model_id)
            raise HTTPException(status_code=500, detail=f"Synthesis failed: {error}")
        finally:
            # Cleared even on failure: a stuck "active" would have every client
            # showing a bar that never moves again.
            state.progress.active = False

    subtype, media_type = SUPPORTED_FORMATS[text_format]
    buffer = io.BytesIO()
    sf.write(buffer, audio, sample_rate, format=subtype)
    return Response(content=buffer.getvalue(), media_type=media_type)


@app.post("/v1/audio/transcriptions", response_model=TranscriptionResponse)
async def create_transcription(
    file: UploadFile = File(..., description="Audio file to transcribe"),
    model: Optional[str] = Form(None),
    language: Optional[str] = Form(None),
    response_format: str = Form("json"),
) -> TranscriptionResponse:
    """Transcribes an uploaded recording.

    The upload is written to a temporary file rather than decoded here: the models
    read a path, and letting the library decode means every container format ffmpeg
    knows is accepted rather than only the ones soundfile reads.
    """
    backend = state.require("transcription")

    if response_format not in ("json", "text"):
        raise HTTPException(
            status_code=400,
            detail=f"'{response_format}' is not supported. Use 'json' or 'text'.",
        )

    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")

    suffix = os.path.splitext(file.filename or "")[1] or ".wav"
    handle, temp_path = tempfile.mkstemp(suffix=suffix)
    os.close(handle)
    try:
        with open(temp_path, "wb") as stream:
            stream.write(payload)

        async with state.lock:
            state.progress = WorkProgress(active=True, chunks_done=0, chunks_total=0)

            def report(done: int, total: int) -> None:
                state.progress.chunks_done = done
                state.progress.chunks_total = total

            try:
                text, used_language = await asyncio.get_running_loop().run_in_executor(
                    None, backend.transcribe, temp_path, language, report
                )
            except Exception as error:
                logger.exception("Transcription failed for %s", state.model_id)
                raise HTTPException(
                    status_code=500, detail=f"Transcription failed: {error}"
                )
            finally:
                # Cleared even on failure: a stuck "active" would leave every
                # client showing a ring that never moves again.
                state.progress.active = False
    finally:
        # The recording is the user's; it must not linger in the container after
        # the request that carried it.
        try:
            os.unlink(temp_path)
        except OSError:
            pass

    return TranscriptionResponse(text=text, language=used_language)


def _synthesize_all(chunks: List[str], voice: Optional[str], speed: float):
    """Speaks every chunk and joins the results with a short gap."""
    pieces = []
    sample_rate = 24000

    for index, chunk in enumerate(chunks):
        audio, sample_rate = state.backend.synthesize(chunk, voice, speed)
        if audio.size == 0:
            continue
        if pieces:
            pieces.append(np.zeros(int(sample_rate * _GAP_SECONDS), dtype=np.float32))
        pieces.append(audio)
        state.progress.chunks_done = index + 1
        logger.info("Synthesised chunk %d/%d", index + 1, len(chunks))

    if not pieces:
        return np.zeros(0, dtype=np.float32), sample_rate
    return np.concatenate(pieces), sample_rate


def load_model(model_id: str, modality: str, trust_remote_code: bool) -> None:
    """Loads the model for one direction, or raises with a legible reason."""
    if modality == "transcription":
        logger.info("Loading %s for transcription", model_id)
        backend = TranscriptionBackend(model_id, trust_remote_code=trust_remote_code)
        backend.load()
        state.model_id = model_id
        state.modality = modality
        state.backend = backend
        logger.info("Ready: transcribing with %s", model_id)
        return

    logger.info("Selecting a backend for %s", model_id)
    backend = select_backend(model_id, trust_remote_code=trust_remote_code)
    logger.info("Loading %s with the %s backend", model_id, backend.name)
    backend.load()

    state.model_id = model_id
    state.modality = modality
    state.backend = backend
    voices = backend.voices()
    logger.info(
        "Ready: serving %s with %d voice%s",
        model_id,
        len(voices),
        "" if len(voices) == 1 else "s",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Kayak audio server")
    parser.add_argument("--model", required=True, help="Hugging Face repository id")
    parser.add_argument(
        "--modality",
        default="speech",
        choices=("speech", "transcription"),
        help="Which direction this server runs in",
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--trust-remote-code",
        action="store_true",
        help="Execute modelling code published in the model repository",
    )
    args = parser.parse_args()

    import uvicorn

    try:
        load_model(args.model, args.modality, args.trust_remote_code)
    except UnsupportedModelError as error:
        # Named as its own failure so the deployment log explains that the model is
        # unsupported rather than showing a library traceback.
        logger.error("UnsupportedModelError: %s", error)
        raise SystemExit(2)
    except Exception as error:
        # The plain sentence is logged before the traceback, and deliberately so:
        # Kayak reports the earliest error line in a dead container's output, while
        # a chained Python traceback leads with its innermost cause. For a misspelled
        # repository that innermost cause is "401 Unauthorized", which reads as a bad
        # access token rather than a name that does not exist.
        logger.error("ModelLoadError: %s", _one_line(error))
        logger.error("Traceback follows.", exc_info=True)
        raise SystemExit(1)

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


def _one_line(error: Exception) -> str:
    """The first line of an exception's message, for a single legible log line."""
    message = str(error).strip() or error.__class__.__name__
    return message.splitlines()[0]


if __name__ == "__main__":
    main()
