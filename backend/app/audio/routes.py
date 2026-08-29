"""The Audio workbench's API.

Kayak proxies the audio servers rather than letting the browser reach them directly.
The servers are published on host ports, which a browser on the same machine can
indeed reach -- but a Kayak opened from another machine cannot, and neither can one
whose servers landed on fallback ports the page never learned about. Going through
Kayak also means the result is stored on the way past, so a clip survives the tab
that made it.
"""

import logging
import wave
from io import BytesIO
from typing import Optional

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from backend.app.audio import store
from backend.app.audio.models import (
    AudioItem,
    AudioKind,
    SpeechRequest,
    Voice,
    VoiceList,
)
from backend.app.audio.store import AudioStoreError
from backend.app.inference import registry
from backend.app.inference.models import Modality, ServerState

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/audio", tags=["audio"])

#: Synthesis of a long article, or transcription of a long recording, runs for
#: minutes on a CPU. The generous ceiling exists to catch a hung server, not to bound
#: honest work.
_REQUEST_TIMEOUT_SECONDS = 1800.0

#: Uploads are held in memory while being forwarded, so the ceiling is real. Two
#: hours of speech comfortably fits; a video file does not, and should not.
MAX_UPLOAD_BYTES = 200 * 1024 * 1024


def _server_base(modality: Modality) -> str:
    """The base URL of a running audio server, or a 409 explaining what to start.

    A 409 rather than a 500: nothing is broken, the user simply has not started the
    model yet, and the message says which one.
    """
    manager = registry.manager_for(modality)
    status = manager.get_status()
    if status.state != ServerState.READY:
        raise HTTPException(
            status_code=409,
            detail=(
                f"No {manager.runtime.label.lower()} model is running. "
                f"Start one from Local Models."
            ),
        )
    return status.endpoint.rstrip("/")


def _model_of(modality: Modality) -> str:
    return registry.manager_for(modality).get_status().model_id or ""


@router.get("/voices", response_model=VoiceList)
async def list_voices() -> VoiceList:
    """Voices the running speech model offers.

    Empty rather than an error when nothing is running: the page shows the same
    "start a model" state either way, and a failed request there reads as a bug.
    """
    try:
        base = _server_base(Modality.SPEECH)
    except HTTPException:
        return VoiceList()

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{base}/audio/voices")
            response.raise_for_status()
            payload = response.json()
    except Exception as error:
        logger.warning("Could not read voices from the speech server: %s", error)
        return VoiceList(model_id=_model_of(Modality.SPEECH))

    # Older images answered with a bare list. Reading both shapes costs three
    # lines and means a mismatched pair degrades rather than breaks.
    entries = payload.get("voices", []) if isinstance(payload, dict) else payload
    default_voice = payload.get("default_voice") if isinstance(payload, dict) else None

    return VoiceList(
        model_id=_model_of(Modality.SPEECH),
        voices=[Voice(**entry) for entry in entries],
        default_voice=default_voice,
    )


@router.post("/speech", response_model=AudioItem)
async def create_speech(request: SpeechRequest) -> AudioItem:
    """Speaks text with the running speech model and stores the clip."""
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="There is no text to speak.")

    base = _server_base(Modality.SPEECH)
    model_id = _model_of(Modality.SPEECH)

    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{base}/audio/speech",
                json={
                    "input": request.text,
                    "voice": request.voice,
                    "speed": request.speed,
                    "response_format": request.response_format,
                },
            )
    except httpx.HTTPError as error:
        raise HTTPException(
            status_code=502, detail=f"The speech server did not respond: {error}"
        )

    if response.status_code != 200:
        raise HTTPException(
            status_code=502, detail=_upstream_detail(response, "Synthesis failed")
        )

    audio = response.content
    try:
        item = store.save_audio(
            data=audio,
            suffix=request.response_format,
            kind=AudioKind.CLIP,
            model_id=model_id,
            text=request.text,
            voice=request.voice,
            duration_seconds=_wav_duration(audio),
        )
    except AudioStoreError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return item


@router.post("/transcriptions", response_model=AudioItem)
async def create_transcription(
    file: UploadFile = File(...),
    language: Optional[str] = Form(None),
) -> AudioItem:
    """Transcribes an uploaded recording with the running transcription model."""
    base = _server_base(Modality.TRANSCRIPTION)
    model_id = _model_of(Modality.TRANSCRIPTION)

    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"That file is {len(payload) / 1024 ** 2:.0f} MB. The limit is "
                f"{MAX_UPLOAD_BYTES // 1024 ** 2} MB."
            ),
        )

    data = {"language": language} if language else None
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{base}/audio/transcriptions",
                files={"file": (file.filename or "recording.wav", payload)},
                data=data,
            )
    except httpx.HTTPError as error:
        raise HTTPException(
            status_code=502, detail=f"The transcription server did not respond: {error}"
        )

    if response.status_code != 200:
        raise HTTPException(
            status_code=502, detail=_upstream_detail(response, "Transcription failed")
        )

    body = response.json()
    return store.save_transcript(
        text=body.get("text", ""),
        model_id=model_id,
        source_filename=file.filename or "recording",
        language=body.get("language") or language,
    )


@router.get("/items", response_model=list[AudioItem])
async def list_items(kind: Optional[AudioKind] = Query(None)) -> list[AudioItem]:
    """Everything the Audio page has produced, newest first."""
    return store.list_items(kind)


@router.get("/items/{item_id}/file")
async def download_item(item_id: str) -> FileResponse:
    """Streams one clip's audio."""
    try:
        item = store.get_item(item_id)
        path = store.audio_path(item)
    except AudioStoreError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error))

    return FileResponse(
        str(path),
        media_type=store.media_type_for(path.suffix),
        filename=path.name,
    )


@router.delete("/items/{item_id}")
async def delete_item(item_id: str) -> dict:
    """Deletes one clip or transcript."""
    try:
        store.delete_item(item_id)
    except AudioStoreError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error))
    return {"status": "deleted", "id": item_id}


def _upstream_detail(response: httpx.Response, fallback: str) -> str:
    """The audio server's own explanation, when it gave one.

    Replacing it with a generic message would hide the only sentence that says what
    went wrong -- an unsupported format, or a model that cannot do this direction.
    """
    try:
        detail = response.json().get("detail")
    except Exception:
        detail = None
    return str(detail) if detail else f"{fallback} ({response.status_code})."


def _wav_duration(data: bytes) -> Optional[float]:
    """Length of a WAV in seconds, when the bytes are a WAV at all.

    Read here rather than in the page: an <audio> element only knows the duration
    once it has loaded the file, so a list of clips would otherwise show nothing
    until each one was played.
    """
    try:
        with wave.open(BytesIO(data)) as handle:
            frames = handle.getnframes()
            rate = handle.getframerate()
            return round(frames / rate, 2) if rate else None
    except Exception:
        return None
