"""The audio work queues.

Synthesis used to be one blocking HTTP request from the browser, and transcription
the same. Leaving the page abandoned the request while the container kept working,
so the work carried on invisibly and a second submission stacked behind it with
nothing saying so.

Jobs run here instead: submitting returns immediately, one job per direction is
processed at a time, and the page reads the queue rather than holding it. Closing
the tab costs nothing -- the result is saved either way.

Speech and transcription have a queue each rather than sharing one, because they
are served by different containers: a recording being transcribed has no reason to
wait behind an article being read aloud.

The queues are in memory. That is the honest scope: the work itself lives in this
process, so a job cannot outlive a backend restart, and persisting the record would
only produce jobs that never finish.
"""

import asyncio
import logging
import time
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional

import httpx

from backend.app.audio import store
from backend.app.audio.models import (
    AudioJob,
    AudioKind,
    JobKind,
    JobState,
    SpeechRequest,
)

logger = logging.getLogger(__name__)

#: How often the worker asks the audio server how far it has got. Chunks take
#: seconds, so this is frequent enough to move a ring and cheap enough to ignore.
PROGRESS_POLL_SECONDS = 0.75

#: Finished jobs kept for the page to show. Older ones fall off; their clips and
#: transcripts remain in the store, which is the durable record.
MAX_FINISHED_JOBS = 30


class JobQueue(ABC):
    """Runs one kind of audio job at a time, tracking progress for each."""

    kind: JobKind

    def __init__(self) -> None:
        self._jobs: Dict[str, AudioJob] = {}
        self._pending: asyncio.Queue = asyncio.Queue()
        self._worker: Optional[asyncio.Task] = None

    def start(self) -> None:
        """Starts the worker, once, for the process lifetime."""
        if self._worker and not self._worker.done():
            return
        self._worker = asyncio.create_task(self._run())

    async def shutdown(self) -> None:
        if self._worker and not self._worker.done():
            self._worker.cancel()
            try:
                await self._worker
            except (asyncio.CancelledError, Exception):
                pass

    def _enqueue(self, job: AudioJob) -> AudioJob:
        self._jobs[job.id] = job
        self._pending.put_nowait(job.id)
        self._trim()
        return job

    def list_jobs(self) -> List[AudioJob]:
        """Every job the page should show, oldest first.

        Oldest first so a queue reads as a queue: the next thing to run is at the
        top, and new submissions appear underneath rather than displacing it.
        """
        return sorted(self._jobs.values(), key=lambda job: job.created_at)

    def has(self, job_id: str) -> bool:
        return job_id in self._jobs

    def cancel(self, job_id: str) -> AudioJob:
        """Drops a job that has not started, or forgets one that has finished.

        A running job is left alone: the container is mid-work and stopping it
        cleanly is not worth the machinery, so cancelling one would only be a lie
        about what happened.

        Raises:
            KeyError: If no such job exists.
            ValueError: If the job is running.
        """
        job = self._jobs[job_id]
        if job.state == JobState.RUNNING:
            raise ValueError("This one is already being worked on; it cannot be cancelled.")
        if job.state == JobState.QUEUED:
            job.state = JobState.CANCELLED
            job.finished_at = time.time()
            self._discard(job)
        else:
            self._jobs.pop(job_id, None)
            self._discard(job)
        return job

    def _discard(self, job: AudioJob) -> None:
        """Releases anything the job was holding. Nothing, by default."""
        return None

    def _trim(self) -> None:
        """Forgets the oldest finished jobs once there are too many."""
        finished = [
            job
            for job in sorted(self._jobs.values(), key=lambda job: job.created_at)
            if job.state in (JobState.DONE, JobState.FAILED, JobState.CANCELLED)
        ]
        for job in finished[: max(0, len(finished) - MAX_FINISHED_JOBS)]:
            self._jobs.pop(job.id, None)
            self._discard(job)

    async def _run(self) -> None:
        while True:
            job_id = await self._pending.get()
            job = self._jobs.get(job_id)
            # Cancelled while it waited, or trimmed away.
            if not job or job.state != JobState.QUEUED:
                continue
            try:
                job.state = JobState.RUNNING
                job.started_at = time.time()
                await self._process(job)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.exception("Audio job %s failed", job.id)
                job.state = JobState.FAILED
                job.error = str(error)
                job.finished_at = time.time()
            finally:
                self._discard(job)

    @abstractmethod
    async def _process(self, job: AudioJob) -> None:
        """Does the work and records the result on the job."""

    async def _watch_progress(
        self,
        client: httpx.AsyncClient,
        base: str,
        job: AudioJob,
        request: asyncio.Task,
    ) -> None:
        """Copies the server's chunk counter onto the job while it works."""
        while not request.done():
            await asyncio.sleep(PROGRESS_POLL_SECONDS)
            try:
                response = await client.get(f"{base}/audio/progress", timeout=5.0)
                if response.status_code != 200:
                    continue
                payload = response.json()
            except Exception:
                # The server is busy; a missed poll is not a failure.
                continue
            if payload.get("active"):
                job.chunks_total = payload.get("chunks_total", 0)
                job.chunks_done = payload.get("chunks_done", 0)

    def _complete(self, job: AudioJob, item_id: str) -> None:
        job.item_id = item_id
        job.state = JobState.DONE
        # A finished job reads better as complete than as "6 of 7": the last chunk
        # is only counted once the response has been assembled.
        if job.chunks_total:
            job.chunks_done = job.chunks_total
        job.finished_at = time.time()
        self._trim()


class SynthesisQueue(JobQueue):
    """Speaks queued text, one piece at a time."""

    kind = JobKind.SPEECH

    def submit(self, request: SpeechRequest, model_id: str) -> AudioJob:
        """Queues one synthesis and returns its job straight away."""
        return self._enqueue(
            AudioJob(
                id=uuid.uuid4().hex,
                kind=JobKind.SPEECH,
                text=request.text,
                voice=request.voice,
                speed=request.speed,
                response_format=request.response_format,
                model_id=model_id,
                state=JobState.QUEUED,
                created_at=time.time(),
            )
        )

    async def _process(self, job: AudioJob) -> None:
        # Imported here rather than at module scope: the routes module imports this
        # one, and the dependency the other way would be a cycle.
        from backend.app.audio.routes import (
            REQUEST_TIMEOUT_SECONDS,
            server_base,
            upstream_detail,
            wav_duration,
        )
        from backend.app.inference.models import Modality

        base = server_base(Modality.SPEECH)

        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            request = asyncio.create_task(
                client.post(
                    f"{base}/audio/speech",
                    json={
                        "input": job.text,
                        "voice": job.voice,
                        "speed": job.speed,
                        "response_format": job.response_format,
                    },
                )
            )
            watcher = asyncio.create_task(self._watch_progress(client, base, job, request))
            try:
                response = await request
            finally:
                watcher.cancel()

        if response.status_code != 200:
            job.state = JobState.FAILED
            job.error = upstream_detail(response, "Synthesis failed")
            job.finished_at = time.time()
            return

        audio = response.content
        item = store.save_audio(
            data=audio,
            suffix=job.response_format,
            kind=AudioKind.CLIP,
            model_id=job.model_id,
            text=job.text,
            voice=job.voice,
            duration_seconds=wav_duration(audio),
        )
        self._complete(job, item.id)


class TranscriptionQueue(JobQueue):
    """Transcribes queued recordings, one at a time."""

    kind = JobKind.TRANSCRIPTION

    def __init__(self) -> None:
        super().__init__()
        # Uploads live on disk until their job runs, so a batch of recordings is
        # not held in memory while it waits its turn.
        self._uploads: Dict[str, Path] = {}

    def submit(
        self,
        *,
        data: bytes,
        filename: str,
        model_id: str,
        language: Optional[str] = None,
    ) -> AudioJob:
        """Queues one recording and returns its job straight away."""
        job_id = uuid.uuid4().hex
        path = store.upload_root() / f"{job_id}-{store.safe_upload_name(filename)}"
        path.write_bytes(data)
        self._uploads[job_id] = path

        return self._enqueue(
            AudioJob(
                id=job_id,
                kind=JobKind.TRANSCRIPTION,
                source_filename=filename,
                language=language,
                model_id=model_id,
                state=JobState.QUEUED,
                created_at=time.time(),
            )
        )

    def _discard(self, job: AudioJob) -> None:
        """Deletes the upload once it can no longer be needed.

        The recording is the user's; it must not linger on disk after the job that
        carried it is over, whether it succeeded, failed or was never run.
        """
        path = self._uploads.pop(job.id, None)
        if path is not None:
            path.unlink(missing_ok=True)

    async def _process(self, job: AudioJob) -> None:
        from backend.app.audio.routes import (
            REQUEST_TIMEOUT_SECONDS,
            server_base,
            upstream_detail,
        )
        from backend.app.inference.models import Modality

        path = self._uploads.get(job.id)
        if path is None or not path.is_file():
            raise FileNotFoundError("The uploaded recording is no longer available.")

        base = server_base(Modality.TRANSCRIPTION)
        payload = path.read_bytes()
        data = {"language": job.language} if job.language else None

        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            request = asyncio.create_task(
                client.post(
                    f"{base}/audio/transcriptions",
                    files={"file": (job.source_filename or "recording.wav", payload)},
                    data=data,
                )
            )
            watcher = asyncio.create_task(self._watch_progress(client, base, job, request))
            try:
                response = await request
            finally:
                watcher.cancel()

        if response.status_code != 200:
            job.state = JobState.FAILED
            job.error = upstream_detail(response, "Transcription failed")
            job.finished_at = time.time()
            return

        body = response.json()
        item = store.save_transcript(
            text=body.get("text", ""),
            model_id=job.model_id,
            source_filename=job.source_filename or "recording",
            language=body.get("language") or job.language,
        )
        job.text = item.text
        self._complete(job, item.id)


synthesis_queue = SynthesisQueue()
transcription_queue = TranscriptionQueue()

#: Every queue, for the routes that address a job without knowing its kind.
QUEUES = (synthesis_queue, transcription_queue)


def all_jobs(kind: Optional[JobKind] = None) -> List[AudioJob]:
    """Every job across the queues, oldest first."""
    jobs: List[AudioJob] = []
    for queue in QUEUES:
        if kind is None or queue.kind == kind:
            jobs.extend(queue.list_jobs())
    return sorted(jobs, key=lambda job: job.created_at)


def cancel_job(job_id: str) -> AudioJob:
    """Cancels or dismisses a job, whichever queue holds it.

    Raises:
        KeyError: If no queue has it.
        ValueError: If it is running.
    """
    for queue in QUEUES:
        if queue.has(job_id):
            return queue.cancel(job_id)
    raise KeyError(job_id)


def start_all() -> None:
    for queue in QUEUES:
        queue.start()


async def shutdown_all() -> None:
    await asyncio.gather(*(queue.shutdown() for queue in QUEUES), return_exceptions=True)
