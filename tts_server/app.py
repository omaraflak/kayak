"""An OpenAI-compatible speech server that serves whatever model it is given.

Started as ``python -m tts_server --model <repo_id>``, exactly the way the vLLM image
is started with a text model. The model is an argument; the image is a runtime.

Endpoints follow OpenAI's audio API so that Kayak talks to speech the same way it
talks to text, and so anything else that speaks that API can use this too:

    GET  /v1/models          the loaded model, which is how readiness is verified
    GET  /v1/audio/voices    what this particular model offers, discovered not listed
    POST /v1/audio/speech    text in, audio out
"""

import argparse
import asyncio
import io
import logging
from typing import List, Optional

import numpy as np
import soundfile as sf
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from tts_server.backends import SpeechBackend, UnsupportedModelError, select_backend
from tts_server.chunking import split_for_synthesis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("tts_server")

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


class ServerState:
    """The single loaded model, and the lock serialising synthesis against it."""

    def __init__(self) -> None:
        self.model_id: str = ""
        self.backend: Optional[SpeechBackend] = None
        # Most of these models are not safe to call concurrently, and a second
        # request arriving mid-generation corrupts the first one's output rather
        # than queueing behind it.
        self.lock = asyncio.Lock()


state = ServerState()
app = FastAPI(title="Kayak speech server")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok" if state.backend else "loading", "model": state.model_id}


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


@app.get("/v1/audio/voices", response_model=List[VoiceInfo])
async def list_voices() -> List[VoiceInfo]:
    """Voices the loaded model offers.

    Served rather than hardcoded anywhere: models differ completely here, and a
    client with its own list would be wrong for every model but one.
    """
    if not state.backend:
        raise HTTPException(status_code=503, detail="The model is still loading.")
    return [
        VoiceInfo(id=voice.id, label=voice.label, language=voice.language)
        for voice in state.backend.voices()
    ]


@app.post("/v1/audio/speech")
async def create_speech(request: SpeechRequest) -> Response:
    """Speaks the given text and returns an audio file."""
    if not state.backend:
        raise HTTPException(status_code=503, detail="The model is still loading.")

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
        try:
            audio, sample_rate = await asyncio.get_running_loop().run_in_executor(
                None, _synthesize_all, chunks, request.voice, request.speed
            )
        except Exception as error:
            logger.exception("Synthesis failed for %s", state.model_id)
            raise HTTPException(status_code=500, detail=f"Synthesis failed: {error}")

    subtype, media_type = SUPPORTED_FORMATS[text_format]
    buffer = io.BytesIO()
    sf.write(buffer, audio, sample_rate, format=subtype)
    return Response(content=buffer.getvalue(), media_type=media_type)


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
        logger.info("Synthesised chunk %d/%d", index + 1, len(chunks))

    if not pieces:
        return np.zeros(0, dtype=np.float32), sample_rate
    return np.concatenate(pieces), sample_rate


def load_model(model_id: str, trust_remote_code: bool) -> None:
    """Selects a backend and loads the model, or exits with a legible reason."""
    logger.info("Selecting a backend for %s", model_id)
    backend = select_backend(model_id, trust_remote_code=trust_remote_code)
    logger.info("Loading %s with the %s backend", model_id, backend.name)
    backend.load()

    state.model_id = model_id
    state.backend = backend
    voices = backend.voices()
    logger.info(
        "Ready: serving %s with %d voice%s",
        model_id,
        len(voices),
        "" if len(voices) == 1 else "s",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Kayak speech server")
    parser.add_argument("--model", required=True, help="Hugging Face repository id")
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
        load_model(args.model, args.trust_remote_code)
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
