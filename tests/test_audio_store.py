"""Tests for the Audio workbench: what it keeps, and what it refuses.

The store hands ids straight from a URL to the filesystem and serves files back, so
the interesting cases are the hostile ones. The routes matter mostly for what they do
when no model is running, which is the state a user is in the first time they open
the page.
"""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from backend.app.audio import store
from backend.app.audio.models import AudioKind
from backend.app.audio.store import AudioStoreError
from backend.app.inference.audio_runtimes import SpeechRuntime, TranscriptionRuntime
from backend.app.inference.models import DeployRequest, Modality
from backend.app.inference.runtimes import SpecContext
from backend.app.main import app


@pytest.fixture
def audio_dir(tmp_path, monkeypatch):
    """Points the store at a scratch directory, never the user's own clips."""
    monkeypatch.setattr(store.settings, "DATA_DIR", tmp_path)
    return tmp_path / "audio"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


WAV_BYTES = b"RIFF\x24\x00\x00\x00WAVEfmt "


class TestStoringClips:
    def test_a_clip_round_trips(self, audio_dir):
        item = store.save_audio(
            data=WAV_BYTES,
            suffix="wav",
            kind=AudioKind.CLIP,
            model_id="hexgrad/Kokoro-82M",
            text="Hello there.",
            voice="af_heart",
        )

        assert store.get_item(item.id).text == "Hello there."
        assert store.audio_path(item).read_bytes() == WAV_BYTES

    def test_the_newest_clip_comes_first(self, audio_dir):
        first = store.save_audio(
            data=WAV_BYTES, suffix="wav", kind=AudioKind.CLIP, model_id="m", text="one"
        )
        second = store.save_audio(
            data=WAV_BYTES, suffix="wav", kind=AudioKind.CLIP, model_id="m", text="two"
        )
        # Written in the same second, so ordering cannot rely on the clock alone.
        store._sidecar(second.id).write_text(
            second.model_copy(update={"created_at": first.created_at + 10}).model_dump_json()
        )

        assert [item.text for item in store.list_items()] == ["two", "one"]

    def test_kinds_can_be_listed_apart(self, audio_dir):
        store.save_audio(
            data=WAV_BYTES, suffix="wav", kind=AudioKind.CLIP, model_id="m", text="spoken"
        )
        store.save_transcript(
            text="heard", model_id="m", source_filename="recording.m4a"
        )

        assert [i.text for i in store.list_items(AudioKind.CLIP)] == ["spoken"]
        assert [i.text for i in store.list_items(AudioKind.TRANSCRIPT)] == ["heard"]

    def test_a_transcript_keeps_no_audio(self, audio_dir):
        # The recording is the user's and they already have it; keeping a copy would
        # double the disk cost of every transcription for nothing.
        item = store.save_transcript(
            text="hello", model_id="m", source_filename="voice-memo.m4a"
        )

        assert item.filename is None
        with pytest.raises(FileNotFoundError):
            store.audio_path(item)

    def test_deleting_removes_the_audio_too(self, audio_dir):
        item = store.save_audio(
            data=WAV_BYTES, suffix="wav", kind=AudioKind.CLIP, model_id="m", text="bye"
        )
        path = store.audio_path(item)

        store.delete_item(item.id)

        assert not path.exists()
        with pytest.raises(FileNotFoundError):
            store.get_item(item.id)

    def test_one_broken_sidecar_does_not_hide_the_rest(self, audio_dir):
        store.save_audio(
            data=WAV_BYTES, suffix="wav", kind=AudioKind.CLIP, model_id="m", text="kept"
        )
        (audio_dir / "halfwritten.json").write_text("{ not json")

        # A crash mid-write must cost one clip, not the whole library.
        assert [item.text for item in store.list_items()] == ["kept"]


class TestStoreRefusals:
    @pytest.mark.parametrize(
        "item_id",
        ["../../../etc/passwd", "..", "a/b", "", "Robert'); DROP TABLE--", "abc.json"],
    )
    def test_ids_that_are_not_ids_are_refused(self, audio_dir, item_id):
        # Ids come from the URL and become filenames; anything but a hex uuid is a
        # traversal attempt, not a typo worth sanitising.
        with pytest.raises(AudioStoreError):
            store.get_item(item_id)

    def test_only_audio_formats_are_stored(self, audio_dir):
        with pytest.raises(AudioStoreError):
            store.save_audio(
                data=b"<script>",
                suffix=".html",
                kind=AudioKind.CLIP,
                model_id="m",
                text="x",
            )


class TestRoutesWithNothingRunning:
    def test_synthesis_says_which_model_to_start(self, client):
        response = client.post("/api/audio/speech", json={"text": "hello"})

        # 409 rather than 500: nothing is broken, the user simply has not started a
        # model, and the message has to say so.
        assert response.status_code == 409
        assert "speech synthesis" in response.json()["detail"].lower()

    def test_transcription_says_which_model_to_start(self, client):
        response = client.post(
            "/api/audio/transcriptions",
            files={"file": ("clip.wav", WAV_BYTES, "audio/wav")},
        )

        assert response.status_code == 409
        assert "transcription" in response.json()["detail"].lower()

    def test_voices_are_empty_rather_than_an_error(self, client):
        # The page shows the same "start a model" state either way, and a failed
        # request there reads as a bug in the page.
        response = client.get("/api/audio/voices")

        assert response.status_code == 200
        assert response.json() == {
            "model_id": None,
            "voices": [],
            "default_voice": None,
        }

    def test_a_malformed_id_is_rejected_not_served(self, client):
        response = client.get("/api/audio/items/..%2F..%2Fetc%2Fpasswd/file")

        assert response.status_code in (400, 404)
        assert "root:" not in response.text


class TestTranscriptionRuntime:
    @pytest.fixture
    def spec(self):
        return asyncio.run(
            TranscriptionRuntime().container_spec(
                DeployRequest(model_id="openai/whisper-small"),
                SpecContext(has_gpu=False, docker_memory_bytes=None, hf_token=None),
            )
        )

    def test_the_server_is_told_which_direction_to_run(self, spec):
        assert spec.command[spec.command.index("--modality") + 1] == "transcription"

    def test_it_shares_the_image_with_speech(self, spec):
        # One image, told at start what it is: they share torch and transformers
        # entirely, and a second image would double a two gigabyte download.
        speech_spec = asyncio.run(
            SpeechRuntime().container_spec(
                DeployRequest(model_id="hexgrad/Kokoro-82M"),
                SpecContext(has_gpu=False, docker_memory_bytes=None, hf_token=None),
            )
        )
        assert spec.image == speech_spec.image

    def test_but_not_the_container_or_the_port(self):
        speech, transcription = SpeechRuntime(), TranscriptionRuntime()

        # Sharing either would mean transcribing a recording costs you the voice
        # model you had loaded.
        assert speech.container_name != transcription.container_name
        assert speech.default_port != transcription.default_port

    def test_it_claims_speech_recognition_repositories(self):
        runtime = TranscriptionRuntime()

        assert runtime.can_serve("openai/whisper-large-v3", library_name="transformers")
        assert runtime.pipeline_tags == ("automatic-speech-recognition",)
