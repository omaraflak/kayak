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

from backend.app.audio import server, store
from backend.app.audio.models import (
    AudioJob,
    AudioKind,
    JobKind,
    JobState,
    SpeechRequest,
)
from backend.app.inference.models import Modality

logger = logging.getLogger(__name__)

#: Finished jobs kept for the page to show. Older ones fall off; their clips and
#: transcripts remain in the store, which is the durable record.
MAX_FINISHED_JOBS = 30


class JobQueue(ABC):
    """Runs one kind of audio job at a time, tracking progress for each."""

    kind: JobKind
    #: The server this queue's work goes to.
    modality: Modality
    #: Said when a job fails in a way nothing more specific explains.
    failure_message: str

    def __init__(self) -> None:
        self._jobs: Dict[str, AudioJob] = {}
        self._pending: asyncio.Queue = asyncio.Queue()
        self._worker: Optional[asyncio.Task] = None
        self._stopping = False

    def start(self) -> None:
        """Starts the worker, once, for the process lifetime."""
        self._stopping = False
        if self._worker and not self._worker.done():
            return
        self._worker = asyncio.create_task(self._run())
        self._worker.add_done_callback(self._on_worker_done)

    async def shutdown(self) -> None:
        self._stopping = True
        if self._worker and not self._worker.done():
            self._worker.cancel()
            try:
                await self._worker
            except (asyncio.CancelledError, Exception):
                pass

    def _on_worker_done(self, worker: "asyncio.Task") -> None:
        """Restarts a worker that ended while Kayak was still running.

        The worker is meant to run for the life of the process. One that ended
        anyway -- an exception nothing caught -- used to take the queue with it:
        every later job sat "waiting" forever, and the one it was working on stayed
        "running".
        """
        if self._stopping:
            return
        if not worker.cancelled() and worker.exception() is not None:
            logger.error(
                "The %s queue's worker stopped; restarting it", self.kind.value,
                exc_info=worker.exception(),
            )
        else:
            logger.error("The %s queue's worker stopped; restarting it", self.kind.value)
        for job in self._jobs.values():
            if job.state == JobState.RUNNING:
                self._fail(job, self.failure_message)
        try:
            self._worker = worker.get_loop().create_task(self._run())
        except RuntimeError:
            # The loop is closing: the process is on its way out anyway.
            return
        self._worker.add_done_callback(self._on_worker_done)

    def _fail(self, job: AudioJob, message: str) -> None:
        job.state = JobState.FAILED
        job.error = message
        job.finished_at = time.time()

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
        """Releases anything the job was holding. Nothing, by default.

        Must not raise: it runs in the worker's cleanup, and from the trimming that
        follows every finished job.
        """
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
                self._fail(job, server.describe_failure(error, self.modality, self.failure_message))
            finally:
                # Cleanup failing must not end the worker with it: that is how one
                # unwritable file used to stop every job queued after it.
                try:
                    self._discard(job)
                except Exception:
                    logger.exception("Could not clean up after audio job %s", job.id)
                if job.state == JobState.RUNNING:
                    self._fail(job, self.failure_message)

    @abstractmethod
    async def _process(self, job: AudioJob) -> None:
        """Does the work and records the result on the job."""

    @staticmethod
    def _progress(job: AudioJob) -> server.ProgressCallback:
        """Copies the server's chunk counter onto the job while it works."""

        def report(done: int, total: int) -> None:
            job.chunks_done, job.chunks_total = done, total

        return report

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
    modality = Modality.SPEECH
    failure_message = "Couldn't finish this clip. Try again."

    def submit(self, request: SpeechRequest, model_id: str) -> AudioJob:
        """Queues one synthesis and returns its job straight away."""
        return self._enqueue(
            AudioJob(
                id=uuid.uuid4().hex,
                kind=JobKind.SPEECH,
                text=request.text,
                voice=request.voice,
                speed=request.speed,
                model_id=model_id,
                state=JobState.QUEUED,
                created_at=time.time(),
            )
        )

    async def _process(self, job: AudioJob) -> None:
        response = await server.post(
            Modality.SPEECH,
            "/audio/speech",
            self._progress(job),
            json={"input": job.text, "voice": job.voice, "speed": job.speed},
        )

        if response.status_code != 200:
            self._fail(job, server.upstream_detail(response, server.SYNTHESIS_FAILED))
            return

        audio = response.content
        item = store.save_audio(
            data=audio,
            suffix="wav",
            kind=AudioKind.CLIP,
            model_id=job.model_id,
            text=job.text,
            voice=job.voice,
            duration_seconds=server.wav_duration(audio),
        )
        self._complete(job, item.id)


class TranscriptionQueue(JobQueue):
    """Transcribes queued recordings, one at a time."""

    kind = JobKind.TRANSCRIPTION
    modality = Modality.TRANSCRIPTION
    failure_message = "Couldn't transcribe this recording. Try again."

    def __init__(self) -> None:
        super().__init__()
        # Uploads live on disk until their job runs, so a batch of recordings is
        # not held in memory while it waits its turn.
        self._uploads: Dict[str, Path] = {}

    def submit(
        self,
        *,
        filename: str,
        model_id: str,
        data: Optional[bytes] = None,
        path: Optional[Path] = None,
        language: Optional[str] = None,
    ) -> AudioJob:
        """Queues one recording and returns its job straight away.

        Args:
            filename: The name the user's file had, shown while it waits.
            model_id: The transcription model it is for.
            data: The recording, for callers that already hold it in memory.
            path: A recording already written to disk with :func:`staging_path`,
                which the job takes over. How uploads arrive: a two-hour recording
                is not something to hold in memory while it waits its turn.
            language: The language spoken, when the user said.
        """
        job_id = uuid.uuid4().hex
        if path is None:
            path = store.upload_root() / f"{job_id}-{store.safe_upload_name(filename)}"
            path.write_bytes(data or b"")
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
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not delete the upload at %s", path, exc_info=True)

    async def _process(self, job: AudioJob) -> None:
        path = self._uploads.get(job.id)
        if path is None or not path.is_file():
            self._fail(
                job,
                "This recording is no longer available. Add it again, then try again.",
            )
            return

        # Sent from the file rather than read into memory first: a long recording
        # is hundreds of megabytes.
        with path.open("rb") as recording:
            response = await server.post(
                Modality.TRANSCRIPTION,
                "/audio/transcriptions",
                self._progress(job),
                files={"file": (job.source_filename or "recording.wav", recording)},
                data={"language": job.language} if job.language else None,
            )

        if response.status_code != 200:
            self._fail(job, server.upstream_detail(response, server.TRANSCRIPTION_FAILED))
            return

        try:
            body = response.json()
        except ValueError:
            logger.warning("The transcription server answered with something that is not JSON")
            self._fail(job, server.TRANSCRIPTION_FAILED)
            return
        item = store.save_transcript(
            text=body.get("text", ""),
            model_id=job.model_id,
            source_filename=job.source_filename or "recording",
            language=body.get("language") or job.language,
        )
        job.text = item.text
        self._complete(job, item.id)


def staging_path(filename: str) -> Path:
    """Where an upload is written before its job is queued.

    In the upload directory already, under a name of its own, so a job takes the
    file over as it is rather than copying it again.
    """
    return store.upload_root() / f"{uuid.uuid4().hex}-{store.safe_upload_name(filename)}"


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
