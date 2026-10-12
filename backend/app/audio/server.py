"""Talking to the local audio servers.

The Audio page's queues and chat's read-aloud share one speech server, and the
transcription queue and dictation share the other. Each server does one piece of
work at a time and reports progress for that one piece, so requests to a server are
serialised here: otherwise a request waiting at the server would be credited with
another's progress, and its timeout would count the wait.
"""

import asyncio
from io import BytesIO
from typing import Any, Callable, Dict, Optional
import wave

from fastapi import HTTPException
import httpx

from backend.app.inference import registry
from backend.app.inference.models import Modality, ServerState

#: Synthesis of a long article, or transcription of a long recording, runs for
#: minutes on a CPU. The generous ceiling exists to catch a hung server, not to bound
#: honest work.
REQUEST_TIMEOUT_SECONDS = 1800.0

#: How often a running request's progress is read. Chunks take seconds, so this is
#: frequent enough to move a ring and cheap enough to ignore.
PROGRESS_POLL_SECONDS = 0.75

#: One request at a time per server.
_locks: Dict[Modality, asyncio.Lock] = {}

ProgressCallback = Callable[[int, int], None]


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


def model_of(modality: Modality) -> str:
    """The repository the server for a modality is serving, or an empty string."""
    return registry.manager_for(modality).get_status().model_id or ""


async def post(
    modality: Modality,
    path: str,
    on_progress: Optional[ProgressCallback] = None,
    **request: Any,
) -> httpx.Response:
    """Posts one piece of work to a server, reporting its progress as it runs.

    Raises:
        HTTPException: 409 if the server is not running.
        httpx.HTTPError: If the server cannot be reached.
    """
    lock = _locks.setdefault(modality, asyncio.Lock())
    async with lock:
        base = server_base(modality)
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            work = asyncio.create_task(client.post(f"{base}{path}", **request))
            watcher = (
                asyncio.create_task(_watch_progress(client, base, work, on_progress))
                if on_progress
                else None
            )
            try:
                return await work
            finally:
                if watcher:
                    watcher.cancel()


async def _watch_progress(
    client: httpx.AsyncClient,
    base: str,
    work: "asyncio.Task[httpx.Response]",
    on_progress: ProgressCallback,
) -> None:
    """Reports the server's chunk counter while a request is in flight."""
    while not work.done():
        await asyncio.sleep(PROGRESS_POLL_SECONDS)
        try:
            response = await client.get(f"{base}/audio/progress", timeout=5.0)
            payload = response.json() if response.status_code == 200 else {}
        except Exception:
            # The server is busy; a missed reading is not a failure.
            continue
        if payload.get("active"):
            on_progress(payload.get("chunks_done", 0), payload.get("chunks_total", 0))


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
