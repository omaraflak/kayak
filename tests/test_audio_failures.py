"""Tests for how audio work fails: in words a person can act on, and without taking
the queue down with it.

A job used to record ``str(error)`` -- "409: No speech synthesis model is running." for
the friendly case, and an empty string for a timeout -- and an exception in its cleanup
ended the worker, leaving every later job "waiting" for ever.
"""

import asyncio
import errno
from pathlib import Path
from typing import List

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.audio import jobs, routes, server, store
from backend.app.audio.models import JobState, SpeechRequest
from backend.app.inference.models import Modality
from backend.app.main import app
from backend.app.user_errors import UserFacingError


@pytest.fixture
def audio_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(store.settings, "DATA_DIR", tmp_path)
    return tmp_path / "audio"


class TestDescribingFailures:
    @pytest.mark.parametrize(
        "error, expected",
        [
            (UserFacingError("Start it first.", 409), "Start it first."),
            (httpx.ReadTimeout("timed out"), server.TIMED_OUT),
            (OSError(errno.ENOSPC, "No space left on device"), server.DISK_FULL),
            (ValueError("unexpected"), "fallback"),
        ],
    )
    def test_known_failures(self, error, expected):
        assert server.describe_failure(error, Modality.SPEECH, "fallback") == expected

    @pytest.mark.parametrize(
        "error",
        [
            httpx.ConnectError("refused"),
            httpx.ConnectTimeout("refused"),
            httpx.RemoteProtocolError("Server disconnected without sending a response."),
            httpx.ReadError("reset"),
        ],
    )
    def test_a_server_that_went_away(self, error):
        message = server.describe_failure(error, Modality.TRANSCRIPTION, "fallback")

        assert message == (
            "The transcription model stopped while working on this. Restart it from "
            "Local Models, then try again."
        )

    def test_not_running_is_a_sentence_and_a_409(self):
        with pytest.raises(UserFacingError) as raised:
            server.server_base(Modality.SPEECH)

        assert raised.value.status_code == 409
        assert raised.value.message == (
            "The speech synthesis model isn't running. Start it from Local Models, then "
            "try again."
        )


class TestUpstreamDetail:
    def test_a_refusal_keeps_the_servers_sentence(self):
        response = httpx.Response(400, json={"detail": "There is no text to speak."})

        assert server.upstream_detail(response, "fallback") == "There is no text to speak."

    def test_a_crash_shows_the_fallback(self):
        response = httpx.Response(500, json={"detail": "Synthesis failed: CUDA error"})

        assert server.upstream_detail(response, "fallback") == "fallback"

    def test_an_unreadable_answer_shows_the_fallback(self):
        assert server.upstream_detail(httpx.Response(502, text="<html>"), "fallback") == "fallback"


class TestTheQueueSurvivesItsJobs:
    def _queue(self, process):
        queue = jobs.SynthesisQueue()
        queue._process = process
        return queue

    def test_a_job_records_the_sentence_not_the_status_code(self):
        async def process(job):
            raise UserFacingError("The speech synthesis model isn't running.", 409)

        async def scenario():
            queue = self._queue(process)
            queue.start()
            job = queue.submit(SpeechRequest(text="hi"), "m")
            for _ in range(20):
                await asyncio.sleep(0)
            await queue.shutdown()
            return job

        job = asyncio.run(scenario())

        assert job.state == JobState.FAILED
        assert job.error == "The speech synthesis model isn't running."

    def test_a_timeout_is_not_an_empty_error(self):
        async def process(job):
            raise httpx.ReadTimeout("")

        async def scenario():
            queue = self._queue(process)
            queue.start()
            job = queue.submit(SpeechRequest(text="hi"), "m")
            for _ in range(20):
                await asyncio.sleep(0)
            await queue.shutdown()
            return job

        job = asyncio.run(scenario())

        assert job.error == server.TIMED_OUT

    def test_a_cleanup_that_raises_does_not_stop_the_next_job(self):
        processed: List[str] = []

        async def process(job):
            processed.append(job.text)
            job.state = JobState.DONE

        async def scenario():
            queue = self._queue(process)

            def broken_discard(job):
                raise PermissionError("cannot delete")

            queue._discard = broken_discard
            queue.start()
            queue.submit(SpeechRequest(text="one"), "m")
            queue.submit(SpeechRequest(text="two"), "m")
            for _ in range(20):
                await asyncio.sleep(0)
            await queue.shutdown()

        asyncio.run(scenario())

        assert processed == ["one", "two"]

    def test_a_dead_worker_is_replaced_and_its_job_failed(self):
        async def scenario():
            queue = jobs.SynthesisQueue()
            started = asyncio.Event()

            async def hang(job):
                started.set()
                await asyncio.sleep(3600)

            queue._process = hang
            queue.start()
            job = queue.submit(SpeechRequest(text="one"), "m")
            await started.wait()
            first_worker = queue._worker
            # Something nobody anticipated ends the worker from outside.
            first_worker.cancel()
            for _ in range(5):
                await asyncio.sleep(0)
            replaced = queue._worker is not first_worker and not queue._worker.done()
            await queue.shutdown()
            return job, replaced

        job, replaced = asyncio.run(scenario())

        assert replaced is True
        assert job.state == JobState.FAILED
        assert job.error


class TestReadAloudSurvivesAFullDisk:
    def test_a_failure_that_cannot_be_saved_still_frees_the_worker(self, audio_dir, monkeypatch):
        from backend.app.audio.speech_cache import SpeechCache

        async def scenario():
            cache = SpeechCache()
            calls: List[str] = []

            async def synthesise(entry):
                calls.append(entry.message_id)
                raise httpx.ConnectError("gone")

            def full_disk(entry):
                raise OSError(errno.ENOSPC, "No space left on device")

            cache._synthesise = synthesise
            cache._persist = full_disk
            cache.start()
            first = cache.request("m1", "hello", None)
            second = cache.request("m2", "again", None)
            for _ in range(20):
                await asyncio.sleep(0)
            await cache.shutdown()
            return calls, first, second

        calls, first, second = asyncio.run(scenario())

        assert calls == ["m1", "m2"]
        assert first.state == "failed"
        assert "stopped while working on this" in (first.error or "")
        assert second.state == "failed"


class TestUploads:
    @pytest.fixture
    def running(self, monkeypatch, audio_dir):
        monkeypatch.setattr(routes, "server_base", lambda modality: "http://audio")
        return audio_dir

    def test_an_oversized_recording_is_refused_and_not_kept(self, running, monkeypatch):
        monkeypatch.setattr(routes, "MAX_UPLOAD_BYTES", 10)

        response = TestClient(app).post(
            "/api/audio/transcriptions",
            files=[("files", ("long.wav", b"x" * 64, "audio/wav"))],
        )

        assert response.status_code == 413
        assert "larger than" in response.json()["detail"]
        assert list((running / "uploads").glob("*")) == []

    def test_a_full_disk_says_so(self, running, monkeypatch):
        def full_disk(source, destination, limit):
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(routes, "_copy_upload", full_disk)

        response = TestClient(app).post(
            "/api/audio/transcriptions",
            files=[("files", ("a.wav", b"RIFF", "audio/wav"))],
        )

        assert response.status_code == 507
        assert response.json()["detail"] == (
            "Your Mac is out of disk space. Free some space, then try again."
        )

    def test_a_recording_is_queued_from_disk(self, running, monkeypatch):
        from backend.app.audio.models import AudioJob, JobKind

        submitted = []

        def submit(**kwargs):
            submitted.append(kwargs)
            return AudioJob(
                id="j", kind=JobKind.TRANSCRIPTION, model_id="m",
                state=JobState.QUEUED, created_at=0,
            )

        monkeypatch.setattr(jobs.transcription_queue, "submit", submit)

        TestClient(app).post(
            "/api/audio/transcriptions",
            files=[("files", ("a.wav", b"RIFF1234", "audio/wav"))],
        )

        assert len(submitted) == 1
        path = submitted[0]["path"]
        assert isinstance(path, Path)
        assert path.read_bytes() == b"RIFF1234"
        assert "data" not in submitted[0]

    def test_dictation_says_what_to_do_when_the_model_is_gone(self, running, monkeypatch):
        async def unreachable(*_args, **_kwargs):
            raise httpx.ConnectError("[Errno 111] Connection refused")

        monkeypatch.setattr(server, "post", unreachable)

        response = TestClient(app).post(
            "/api/audio/listen", files={"file": ("d.webm", b"webm", "audio/webm")}
        )

        assert response.status_code == 502
        assert response.json()["detail"] == (
            "Kayak couldn't reach the transcription model. Restart it from Local Models, "
            "then try again."
        )
        assert list((running / "uploads").glob("*")) == []

    def test_dictation_survives_an_unreadable_answer(self, running, monkeypatch):
        async def garbled(*_args, **_kwargs):
            return httpx.Response(200, text="not json")

        monkeypatch.setattr(server, "post", garbled)

        response = TestClient(app).post(
            "/api/audio/listen", files={"file": ("d.webm", b"webm", "audio/webm")}
        )

        assert response.status_code == 502
        assert response.json()["detail"] == server.TRANSCRIPTION_FAILED

    def test_a_missing_item_is_a_sentence(self, audio_dir):
        response = TestClient(app).get(f"/api/audio/items/{'a' * 32}/file")

        assert response.status_code == 404
        assert response.json()["detail"] == (
            "This recording is no longer available. It may have been deleted."
        )
