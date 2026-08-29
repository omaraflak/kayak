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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import httpx

from backend.app.config import settings

logger = logging.getLogger(__name__)

#: How long a synthesised message is kept. Long enough to reread a conversation
#: from this morning, short enough that it never becomes storage anyone manages.
TTL_SECONDS = 24 * 60 * 60

#: How often expired entries are swept off disk.
SWEEP_INTERVAL_SECONDS = 60 * 60

PROGRESS_POLL_SECONDS = 0.6


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

    # --- lifecycle -------------------------------------------------------

    def start(self) -> None:
        if not self._worker or self._worker.done():
            self._worker = asyncio.create_task(self._run())
        if not self._sweeper or self._sweeper.done():
            self._sweeper = asyncio.create_task(self._sweep_forever())
        self._load_from_disk()

    async def shutdown(self) -> None:
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
            raise FileNotFoundError(f"No speech stored for message '{message_id}'.")
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
            try:
                await self._synthesise(entry)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.exception("Speech for message %s failed", message_id)
                entry.state = "failed"
                entry.error = str(error)
                self._persist(entry)

    async def _synthesise(self, entry: SpeechEntry) -> None:
        from backend.app.audio.routes import (
            REQUEST_TIMEOUT_SECONDS,
            server_base,
            upstream_detail,
        )
        from backend.app.inference.models import Modality

        text, voice = self._texts.get(entry.message_id, ("", None))
        base = server_base(Modality.SPEECH)

        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            request = asyncio.create_task(
                client.post(
                    f"{base}/audio/speech",
                    json={"input": text, "voice": voice, "response_format": "wav"},
                )
            )
            watcher = asyncio.create_task(self._watch(client, base, entry, request))
            try:
                response = await request
            finally:
                watcher.cancel()

        if response.status_code != 200:
            entry.state = "failed"
            entry.error = upstream_detail(response, "Synthesis failed")
            self._persist(entry)
            return

        self._audio_path(entry.message_id).write_bytes(response.content)
        entry.state = "ready"
        if entry.chunks_total:
            entry.chunks_done = entry.chunks_total
        self._persist(entry)
        self._texts.pop(entry.message_id, None)

    async def _watch(self, client, base: str, entry: SpeechEntry, request) -> None:
        while not request.done():
            await asyncio.sleep(PROGRESS_POLL_SECONDS)
            try:
                reading = await client.get(f"{base}/audio/progress", timeout=5.0)
                if reading.status_code != 200:
                    continue
                payload = reading.json()
            except Exception:
                continue
            if payload.get("active"):
                entry.chunks_total = payload.get("chunks_total", 0)
                entry.chunks_done = payload.get("chunks_done", 0)

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
