"""Synthesised speech for chat messages, kept for a day.

Reading a reply aloud used to be a request whose result existed only in the tab
that asked for it. Leaving the conversation or reloading the page threw away work
the server had already done, and there was nothing left to show that a message was
still being synthesised.

Speech is stored against the message it belongs to instead. That makes three
things fall out: a reload can show what is still in flight, asking for the same
message twice costs nothing the second time, and audio nobody asked to play is
never played -- the page decides that, not the presence of a result.

Entries expire after a day. They are a convenience, not a library: the Audio page
is where audio is kept deliberately.
"""

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from backend.app.audio import server
from backend.app.config import settings
from backend.app.inference.models import Modality

logger = logging.getLogger(__name__)

#: How long a synthesised message is kept. Long enough to reread a conversation
#: from this morning, short enough that it never becomes storage anyone manages.
TTL_SECONDS = 24 * 60 * 60

#: How often expired entries are swept off disk.
SWEEP_INTERVAL_SECONDS = 60 * 60

#: Said when reading a message aloud fails for a reason nothing more specific names.
FAILED = "Couldn't read this aloud. Try again."


@dataclass
class SpeechEntry:
    """One message's speech, or the attempt to produce it."""
    message_id: str
    #: pending, ready or failed.
    state: str
    created_at: float
    #: Identifies what was spoken. A message that was edited, or a different
    #: voice, is different audio and must not be served from the old file.
    fingerprint: str = ""
    chunks_done: int = 0
    chunks_total: int = 0
    error: Optional[str] = None

    def is_expired(self, now: Optional[float] = None) -> bool:
        return (now or time.time()) - self.created_at > TTL_SECONDS


def fingerprint_for(text: str, voice: Optional[str]) -> str:
    """What identifies a piece of speech: its words and the voice speaking them."""
    digest = hashlib.sha256()
    digest.update(text.strip().encode("utf-8"))
    digest.update(b"\x00")
    digest.update((voice or "").encode("utf-8"))
    return digest.hexdigest()


class SpeechCache:
    """Synthesises message speech once, and keeps it for a day."""

    def __init__(self) -> None:
        self._entries: Dict[str, SpeechEntry] = {}
        self._pending: asyncio.Queue = asyncio.Queue()
        self._worker: Optional[asyncio.Task] = None
        self._sweeper: Optional[asyncio.Task] = None
        #: Text waiting to be spoken, by message. Held apart from the entry so the
        #: entry stays a description of state rather than a copy of the message.
        self._texts: Dict[str, tuple] = {}
        #: The message being spoken right now, so a worker that dies mid-way does
        #: not leave it "pending" forever.
        self._current: Optional[str] = None
        self._stopping = False

    # --- lifecycle -------------------------------------------------------

    def start(self) -> None:
        self._stopping = False
        if not self._worker or self._worker.done():
            self._worker = asyncio.create_task(self._run())
            self._worker.add_done_callback(self._on_worker_done)
        if not self._sweeper or self._sweeper.done():
            self._sweeper = asyncio.create_task(self._sweep_forever())
        self._load_from_disk()

    def _on_worker_done(self, worker: "asyncio.Task") -> None:
        """Restarts a worker that ended while Kayak was still running.

        Without this, one uncaught failure ended read-aloud for the rest of the
        session: every later request stayed "Preparing…" with nothing to serve it.
        """
        if self._stopping:
            return
        logger.error(
            "The read-aloud worker stopped; restarting it",
            exc_info=None if worker.cancelled() else worker.exception(),
        )
        entry = self._entries.get(self._current or "")
        if entry is not None and entry.state == "pending":
            self._mark_failed(entry, FAILED)
        self._current = None
        try:
            self._worker = worker.get_loop().create_task(self._run())
        except RuntimeError:
            return
        self._worker.add_done_callback(self._on_worker_done)

    async def shutdown(self) -> None:
        self._stopping = True
        for task in (self._worker, self._sweeper):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    # --- storage ---------------------------------------------------------

    def root(self) -> Path:
        path = settings.DATA_DIR / "audio" / "speech"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _audio_path(self, message_id: str) -> Path:
        return self.root() / f"{_safe(message_id)}.wav"

    def _sidecar(self, message_id: str) -> Path:
        return self.root() / f"{_safe(message_id)}.json"

    def _load_from_disk(self) -> None:
        """Rebuilds the index from what survived a restart, dropping the stale."""
        now = time.time()
        for sidecar in self.root().glob("*.json"):
            try:
                payload = json.loads(sidecar.read_text(encoding="utf-8"))
                entry = SpeechEntry(**payload)
            except Exception:
                sidecar.unlink(missing_ok=True)
                continue
            # A pending entry cannot survive the process that was producing it.
            if entry.state == "pending" or entry.is_expired(now):
                self._forget(entry.message_id)
                continue
            self._entries[entry.message_id] = entry

    def _persist(self, entry: SpeechEntry) -> None:
        self._sidecar(entry.message_id).write_text(
            json.dumps(entry.__dict__), encoding="utf-8"
        )

    def _mark_failed(self, entry: SpeechEntry, message: str) -> None:
        """Records a failure, keeping it in memory even if it cannot be saved.

        The record on disk only matters after a restart; the page asking right now
        reads the entry in memory. A full disk -- the likeliest reason a write fails
        -- must not turn a reported failure into a worker that dies.
        """
        entry.state = "failed"
        entry.error = message
        try:
            self._persist(entry)
        except OSError:
            logger.warning("Could not save the failure for message %s", entry.message_id, exc_info=True)

    def _forget(self, message_id: str) -> None:
        self._entries.pop(message_id, None)
        self._texts.pop(message_id, None)
        self._audio_path(message_id).unlink(missing_ok=True)
        self._sidecar(message_id).unlink(missing_ok=True)

    # --- the interesting part -------------------------------------------

    def get(self, message_id: str) -> Optional[SpeechEntry]:
        entry = self._entries.get(message_id)
        if entry and entry.is_expired():
            self._forget(message_id)
            return None
        return entry

    def states(self, message_ids: List[str]) -> Dict[str, SpeechEntry]:
        return {
            message_id: entry
            for message_id in message_ids
            if (entry := self.get(message_id)) is not None
        }

    def audio_path(self, message_id: str) -> Path:
        """Where a ready entry's audio lives.

        Raises:
            FileNotFoundError: If it was never made, expired, or failed.
        """
        entry = self.get(message_id)
        path = self._audio_path(message_id)
        if not entry or entry.state != "ready" or not path.is_file():
            raise FileNotFoundError(f"No speech stored for message {message_id!r}.")
        return path

    def request(self, message_id: str, text: str, voice: Optional[str]) -> SpeechEntry:
        """Ensures this message has speech, and says where that stands.

        Asking twice for the same words is free: the second caller is handed the
        first one's result, or joins it in progress.
        """
        fingerprint = fingerprint_for(text, voice)
        existing = self.get(message_id)

        # A failed attempt is not reused: the container may have been restarted
        # since, and the whole point of asking again is to try again.
        if existing and existing.fingerprint == fingerprint and existing.state != "failed":
            return existing

        # Different words or a different voice: the old audio describes neither.
        if existing:
            self._forget(message_id)

        entry = SpeechEntry(
            message_id=message_id,
            state="pending",
            created_at=time.time(),
            fingerprint=fingerprint,
        )
        self._entries[message_id] = entry
        self._texts[message_id] = (text, voice)
        self._pending.put_nowait(message_id)
        return entry

    async def _run(self) -> None:
        while True:
            message_id = await self._pending.get()
            entry = self._entries.get(message_id)
            if not entry or entry.state != "pending":
                continue
            self._current = message_id
            try:
                await self._synthesise(entry)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.exception("Speech for message %s failed", message_id)
                self._mark_failed(
                    entry, server.describe_failure(error, Modality.SPEECH, FAILED)
                )
            finally:
                self._current = None

    async def _synthesise(self, entry: SpeechEntry) -> None:
        text, voice = self._texts.get(entry.message_id, ("", None))

        def report(done: int, total: int) -> None:
            entry.chunks_done, entry.chunks_total = done, total

        response = await server.post(
            Modality.SPEECH, "/audio/speech", report, json={"input": text, "voice": voice}
        )

        if response.status_code != 200:
            self._mark_failed(entry, server.upstream_detail(response, server.SYNTHESIS_FAILED))
            return

        self._audio_path(entry.message_id).write_bytes(response.content)
        entry.state = "ready"
        if entry.chunks_total:
            entry.chunks_done = entry.chunks_total
        self._texts.pop(entry.message_id, None)
        try:
            self._persist(entry)
        except OSError:
            # The audio is saved and plays now; only remembering it across a
            # restart is lost, which is no reason to report the work as failed.
            logger.warning("Could not save the record for message %s", entry.message_id, exc_info=True)

    async def _sweep_forever(self) -> None:
        while True:
            try:
                self.sweep()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Speech cache sweep failed")
            await asyncio.sleep(SWEEP_INTERVAL_SECONDS)

    def sweep(self) -> int:
        """Deletes everything past its day. Returns how many went."""
        now = time.time()
        expired = [
            message_id
            for message_id, entry in list(self._entries.items())
            if entry.is_expired(now)
        ]
        for message_id in expired:
            self._forget(message_id)
        return len(expired)


def _safe(message_id: str) -> str:
    """A message id reduced to something that cannot climb out of a directory."""
    return "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in message_id
    )[:120] or "unknown"


speech_cache = SpeechCache()
