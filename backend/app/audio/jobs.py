"""The synthesis queue.

Synthesis used to be one blocking HTTP request from the browser. Leaving the page
abandoned the request while the container kept generating, so the work carried on
invisibly and a second submission queued behind it with nothing saying so.

Jobs run here instead: submitting returns immediately, one job is processed at a
time, and the page reads the queue rather than holding it. Closing the tab, or
navigating away, costs nothing -- the clip is saved when it finishes either way.

The queue is in memory. That is the honest scope: the work itself lives in this
process, so a job cannot outlive a backend restart, and pretending otherwise by
persisting the record would only produce jobs that never finish.
"""

import asyncio
import logging
import time
import uuid
from typing import Dict, List, Optional

import httpx

from backend.app.audio import store
from backend.app.audio.models import (
    AudioKind,
    JobState,
    SpeechRequest,
    SynthesisJob,
)

logger = logging.getLogger(__name__)

#: How often the worker asks the audio server how far it has got. Chunks take
#: seconds, so this is frequent enough to move a bar and cheap enough to ignore.
PROGRESS_POLL_SECONDS = 0.75

#: Finished jobs kept for the page to show. Older ones fall off; their clips
#: remain in the store, which is the durable record.
MAX_FINISHED_JOBS = 30


class SynthesisQueue:
    """Runs synthesis jobs one at a time, tracking progress for each."""

    def __init__(self) -> None:
        self._jobs: Dict[str, SynthesisJob] = {}
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

    def submit(self, request: SpeechRequest, model_id: str) -> SynthesisJob:
        """Queues one synthesis and returns its job straight away."""
        job = SynthesisJob(
            id=uuid.uuid4().hex,
            text=request.text,
            voice=request.voice,
            speed=request.speed,
            response_format=request.response_format,
            model_id=model_id,
            state=JobState.QUEUED,
            created_at=time.time(),
        )
        self._jobs[job.id] = job
        self._pending.put_nowait(job.id)
        self._trim()
        return job

    def list_jobs(self) -> List[SynthesisJob]:
        """Every job the page should show, oldest first.

        Oldest first so a queue reads as a queue: the next thing to run is at the
        top, and new submissions appear underneath rather than displacing it.
        """
        return sorted(self._jobs.values(), key=lambda job: job.created_at)

    def cancel(self, job_id: str) -> SynthesisJob:
        """Drops a job that has not started, or forgets one that has finished.

        A running job is left alone: the container is mid-generation and stopping
        it cleanly is not worth the machinery, so cancelling one would only be a
        lie about what happened.

        Raises:
            KeyError: If no such job exists.
            ValueError: If the job is running.
        """
        job = self._jobs[job_id]
        if job.state == JobState.RUNNING:
            raise ValueError("This one is already being spoken; it cannot be cancelled.")
        if job.state == JobState.QUEUED:
            job.state = JobState.CANCELLED
            job.finished_at = time.time()
        else:
            self._jobs.pop(job_id, None)
        return job

    def _trim(self) -> None:
        """Forgets the oldest finished jobs once there are too many."""
        finished = [
            job
            for job in sorted(self._jobs.values(), key=lambda job: job.created_at)
            if job.state in (JobState.DONE, JobState.FAILED, JobState.CANCELLED)
        ]
        for job in finished[: max(0, len(finished) - MAX_FINISHED_JOBS)]:
            self._jobs.pop(job.id, None)

    async def _run(self) -> None:
        while True:
            job_id = await self._pending.get()
            job = self._jobs.get(job_id)
            # Cancelled while it waited, or trimmed away.
            if not job or job.state != JobState.QUEUED:
                continue
            try:
                await self._process(job)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.exception("Synthesis job %s failed", job.id)
                job.state = JobState.FAILED
                job.error = str(error)
                job.finished_at = time.time()

    async def _process(self, job: SynthesisJob) -> None:
        # Imported here rather than at module scope: the routes module imports this
        # one, and the dependency the other way would be a cycle.
        from backend.app.audio.routes import (
            REQUEST_TIMEOUT_SECONDS,
            server_base,
            upstream_detail,
            wav_duration,
        )
        from backend.app.inference.models import Modality

        job.state = JobState.RUNNING
        job.started_at = time.time()

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

        job.item_id = item.id
        job.state = JobState.DONE
        # A finished job reads better as complete than as "6 of 7": the last chunk
        # is only counted once the response has been assembled.
        if job.chunks_total:
            job.chunks_done = job.chunks_total
        job.finished_at = time.time()
        self._trim()

    async def _watch_progress(
        self,
        client: httpx.AsyncClient,
        base: str,
        job: SynthesisJob,
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
                # The server is busy generating; a missed poll is not a failure.
                continue
            if payload.get("active"):
                job.chunks_total = payload.get("chunks_total", 0)
                job.chunks_done = payload.get("chunks_done", 0)


synthesis_queue = SynthesisQueue()
