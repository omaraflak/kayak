"""Talking to the local audio servers.

The Audio page's queues and chat's read-aloud share one speech server, and the
transcription queue and dictation share the other. Each server does one piece of
work at a time and reports progress for that one piece, so requests to a server are
serialised here: otherwise a request waiting at the server would be credited with
another's progress, and its timeout would count the wait.
"""

import asyncio
import errno
from io import BytesIO
import logging
from typing import Any, Callable, Dict, Optional
import wave

import httpx

from backend.app.inference import registry
from backend.app.inference.models import Modality, ServerState
from backend.app.user_errors import UserFacingError

logger = logging.getLogger(__name__)

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


#: What each direction's failures are called, when the response carries nothing
#: better. Phrased as what did not happen and what to try next.
SYNTHESIS_FAILED = (
    "The voice model couldn't read this text aloud. Try again. If it keeps failing, "
    "restart it from Local Models."
)
TRANSCRIPTION_FAILED = (
    "Kayak couldn't turn this recording into text. Make sure it's an audio file (like "
    ".mp3, .m4a or .wav), then try again."
)
DISK_FULL = "Kayak couldn't save the result because your Mac is low on disk space."
TIMED_OUT = (
    "This took too long and was stopped. Try something shorter, or restart the model "
    "from Local Models."
)


def label_of(modality: Modality) -> str:
    """How a person would name the model serving one direction: "speech synthesis"."""
    return registry.manager_for(modality).runtime.label.lower()


def not_running(modality: Modality) -> UserFacingError:
    """The refusal for work sent to a model that is not running.

    A 409 rather than a 500: nothing is broken, the user simply has not started the
    model yet, and the message says which one.
    """
    return UserFacingError(
        f"The {label_of(modality)} model isn't running. Start it from Local Models, "
        "then try again.",
        409,
    )


def server_base(modality: Modality) -> str:
    """The base URL of a running audio server.

    Raises:
        UserFacingError: 409 when the model is not running. Raised as a
            UserFacingError rather than an HTTPException because the work queues
            call this too, and record the message as the job's error.
    """
    manager = registry.manager_for(modality)
    status = manager.get_status()
    if status.state != ServerState.READY:
        raise not_running(modality)
    return status.endpoint.rstrip("/")


def describe_failure(error: BaseException, modality: Modality, fallback: str) -> str:
    """The sentence for a piece of audio work that raised, for the person waiting on it.

    Recorded as a job's error, where ``str(error)`` used to put "409: " in front of a
    message, or nothing at all for a timeout, whose text is empty.

    Args:
        error: What the work raised.
        modality: Which server the work was sent to.
        fallback: What to say when the error is none of the known kinds.
    """
    if isinstance(error, UserFacingError):
        return error.message
    if isinstance(error, httpx.ConnectTimeout):
        return (
            f"The {label_of(modality)} model stopped while working on this. Restart it "
            "from Local Models, then try again."
        )
    if isinstance(error, httpx.TimeoutException):
        return TIMED_OUT
    if isinstance(error, httpx.TransportError):
        return (
            f"The {label_of(modality)} model stopped while working on this. Restart it "
            "from Local Models, then try again."
        )
    if isinstance(error, OSError) and error.errno in (errno.ENOSPC, errno.EDQUOT):
        return DISK_FULL
    return fallback


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
        UserFacingError: 409 if the server is not running.
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
    """The audio server's own explanation of a refusal, when it gave one.

    A refusal (4xx) carries a sentence about the request -- "There is no text to
    speak." -- that says more than any fallback could, so it is passed on. A failure
    (5xx) is the server's own trouble, and its text is not for the person waiting:
    it is logged, and the fallback, which says what to do, is shown instead.

    Args:
        response: The audio server's answer.
        fallback: A full sentence saying what failed and what to do next.
    """
    try:
        detail = response.json().get("detail")
    except Exception:
        detail = None
    if response.status_code >= 500 or not isinstance(detail, str) or not detail.strip():
        logger.warning(
            "Audio server answered %s: %s", response.status_code, detail or response.text[:500]
        )
        return fallback
    return detail.strip()


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
