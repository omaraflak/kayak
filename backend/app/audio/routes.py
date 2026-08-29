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
from backend.app.audio import jobs as job_queues
from backend.app.audio.models import (
    AudioItem,
    AudioJob,
    AudioKind,
    JobKind,
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
REQUEST_TIMEOUT_SECONDS = 1800.0

#: Uploads are held in memory while being forwarded, so the ceiling is real. Two
#: hours of speech comfortably fits; a video file does not, and should not.
MAX_UPLOAD_BYTES = 200 * 1024 * 1024


def server_base(modality: Modality) -> str:
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
        base = server_base(Modality.SPEECH)
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


@router.post("/speech", response_model=AudioJob)
async def create_speech(request: SpeechRequest) -> AudioJob:
    """Queues text to be spoken and returns the job immediately.

    Deliberately not the audio: synthesis runs for minutes, and holding the
    request open tied the work to a tab. The caller watches ``GET /jobs``; the
    clip is saved whether or not anyone is still looking.
    """
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="There is no text to speak.")

    # Checked before queueing so an unstartable job is refused now rather than
    # sitting in the queue to fail later.
    server_base(Modality.SPEECH)
    return job_queues.synthesis_queue.submit(request, _model_of(Modality.SPEECH))


@router.get("/jobs", response_model=list[AudioJob])
async def list_jobs(kind: Optional[JobKind] = Query(None)) -> list[AudioJob]:
    """The queues: what is waiting, what is running, and how far it has got."""
    return job_queues.all_jobs(kind)


@router.delete("/jobs/{job_id}")
async def cancel_job(job_id: str) -> dict:
    """Removes a queued job, or dismisses a finished one."""
    try:
        job = job_queues.cancel_job(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="No such job.")
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error))
    return {"status": job.state.value, "id": job_id}


@router.post("/transcriptions", response_model=list[AudioJob])
async def create_transcriptions(
    files: list[UploadFile] = File(...),
    language: Optional[str] = Form(None),
) -> list[AudioJob]:
    """Queues one or more recordings to be transcribed.

    A list rather than a single file: transcribing an afternoon of recordings is
    the ordinary case, and doing them one upload at a time made the user the queue.
    """
    base_checked = server_base(Modality.TRANSCRIPTION)  # noqa: F841 - refuses early
    model_id = _model_of(Modality.TRANSCRIPTION)

    queued: list[AudioJob] = []
    for upload in files:
        payload = await upload.read()
        if not payload:
            raise HTTPException(
                status_code=400,
                detail=f"'{upload.filename or 'A file'}' is empty.",
            )
        if len(payload) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"'{upload.filename}' is {len(payload) / 1024 ** 2:.0f} MB. "
                    f"The limit is {MAX_UPLOAD_BYTES // 1024 ** 2} MB."
                ),
            )
        queued.append(
            job_queues.transcription_queue.submit(
                data=payload,
                filename=upload.filename or "recording.wav",
                model_id=model_id,
                language=language,
            )
        )

    return queued


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


def upstream_detail(response: httpx.Response, fallback: str) -> str:
    """The audio server's own explanation, when it gave one.

    Replacing it with a generic message would hide the only sentence that says what
    went wrong -- an unsupported format, or a model that cannot do this direction.
    """
    try:
        detail = response.json().get("detail")
    except Exception:
        detail = None
    return str(detail) if detail else f"{fallback} ({response.status_code})."


def wav_duration(data: bytes) -> Optional[float]:
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
