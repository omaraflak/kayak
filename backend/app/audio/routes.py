"""The Audio workbench's API.

Kayak proxies the audio servers rather than letting the browser reach them directly.
The servers are published on host ports, which a browser on the same machine can
indeed reach -- but a Kayak opened from another machine cannot, and neither can one
whose servers landed on fallback ports the page never learned about. Going through
Kayak also means the result is stored on the way past, so a clip survives the tab
that made it.
"""

import logging
from typing import Optional

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from backend.app.audio import jobs as job_queues
from backend.app.audio import server, store
from backend.app.audio.server import model_of, server_base
from backend.app.audio.speech_cache import speech_cache
from backend.app.audio.models import (
    AudioItem,
    AudioJob,
    AudioKind,
    JobKind,
    MessageSpeech,
    SpeechRequest,
    Voice,
    VoiceList,
)
from backend.app.audio.store import AudioStoreError
from backend.app.inference.models import Modality

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/audio", tags=["audio"])

#: Uploads are held in memory while being forwarded, so the ceiling is real. Two
#: hours of speech comfortably fits; a video file does not, and should not.
MAX_UPLOAD_BYTES = 200 * 1024 * 1024


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
        return VoiceList(model_id=model_of(Modality.SPEECH))

    return VoiceList(
        model_id=model_of(Modality.SPEECH),
        voices=[Voice(**entry) for entry in payload.get("voices", [])],
        default_voice=payload.get("default_voice"),
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
    return job_queues.synthesis_queue.submit(request, model_of(Modality.SPEECH))


@router.post("/messages/{message_id}/speech", response_model=MessageSpeech)
async def request_message_speech(message_id: str, request: SpeechRequest) -> MessageSpeech:
    """Ensures a chat message has speech, and says where that stands.

    Returns immediately rather than the audio: synthesis takes as long as the
    speech does, and a request held open for it dies with the tab. Asking twice
    is free -- the second caller joins the first, or is handed its result.
    """
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="There is no text to speak.")

    # Refused now rather than left pending forever with no model to serve it.
    server_base(Modality.SPEECH)
    entry = speech_cache.request(message_id, request.text, request.voice)
    return _as_message_speech(entry)


@router.get("/messages/speech", response_model=list[MessageSpeech])
async def get_message_speech(ids: str = Query("")) -> list[MessageSpeech]:
    """What speech exists, or is being made, for these messages.

    Asked when a conversation is opened so that work already in flight is visible
    again after a reload, rather than the page forgetting it was ever started.
    """
    wanted = [item for item in ids.split(",") if item]
    return [_as_message_speech(entry) for entry in speech_cache.states(wanted).values()]


@router.get("/messages/{message_id}/speech/audio")
async def get_message_speech_audio(message_id: str) -> FileResponse:
    """The audio for one message, once it is ready."""
    try:
        path = speech_cache.audio_path(message_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error))
    return FileResponse(str(path), media_type="audio/wav")


def _as_message_speech(entry) -> MessageSpeech:
    return MessageSpeech(
        message_id=entry.message_id,
        state=entry.state,
        chunks_done=entry.chunks_done,
        chunks_total=entry.chunks_total,
        error=entry.error,
    )


@router.post("/listen")
async def listen(file: UploadFile = File(...)) -> dict:
    """Transcribes a recording and returns the text, without storing anything.

    For dictating into a message box, where the text is the point and the audio
    was never meant to be kept.
    """
    server_base(Modality.TRANSCRIPTION)

    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="The recording is empty.")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="That recording is too long.")

    try:
        response = await server.post(
            Modality.TRANSCRIPTION,
            "/audio/transcriptions",
            files={"file": (file.filename or "dictation.webm", payload)},
        )
    except httpx.HTTPError as error:
        raise HTTPException(
            status_code=502, detail=f"The transcription server did not respond: {error}"
        )

    if response.status_code != 200:
        raise HTTPException(
            status_code=502, detail=server.upstream_detail(response, "Transcription failed")
        )

    return {"text": response.json().get("text", "")}


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
    server_base(Modality.TRANSCRIPTION)
    model_id = model_of(Modality.TRANSCRIPTION)

    # Every file is checked before any is queued: refusing the third after queueing
    # the first two would leave work running that the caller was told failed.
    payloads: list[tuple[str, bytes]] = []
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
        payloads.append((upload.filename or "recording.wav", payload))

    return [
        job_queues.transcription_queue.submit(
            data=payload, filename=filename, model_id=model_id, language=language
        )
        for filename, payload in payloads
    ]


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
