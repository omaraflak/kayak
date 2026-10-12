"""Tests for the modality-scoped serving API.

The routes carry a modality, which introduces a way to get the URL space wrong:
`/{modality}/status` has the same shape as fixed routes such as `/metal`, and
FastAPI matches in declaration order.
"""

import pytest
from fastapi.testclient import TestClient

from backend.app.inference import metal
from backend.app.inference.models import MetalStatus, Modality
from backend.app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestRuntimeDiscovery:
    def test_every_modality_is_described(self, client):
        response = client.get("/api/inference/runtimes")

        assert response.status_code == 200
        described = {entry["modality"] for entry in response.json()}
        assert described == {modality.value for modality in Modality}

    def test_a_runtime_declares_what_it_serves_and_what_it_accepts(self, client):
        runtimes = {entry["modality"]: entry for entry in client.get("/api/inference/runtimes").json()}

        speech = runtimes["speech"]
        # The catalogue reads its filter and its "can this be started" test from
        # here; anything hardcoded in a client would go stale on the next backend.
        assert speech["pipeline_tags"] == ["text-to-speech"]
        assert "kokoro" in speech["supported_id_fragments"]
        # Settings a speech model has no notion of must not be offered for it.
        assert "max_model_len" not in speech["tunable_fields"]
        assert "max_model_len" in runtimes["text"]["tunable_fields"]


class TestStatus:
    def test_status_reports_every_server_keyed_by_modality(self, client):
        response = client.get("/api/inference/status")

        assert response.status_code == 200
        payload = response.json()
        assert set(payload) == {modality.value for modality in Modality}
        for modality, status in payload.items():
            assert status["modality"] == modality

    def test_one_server_can_be_asked_for_by_name(self, client):
        response = client.get("/api/inference/speech/status")

        assert response.status_code == 200
        assert response.json()["modality"] == "speech"

    def test_an_unknown_modality_is_rejected(self, client):
        response = client.get("/api/inference/interpretive-dance/status")

        assert response.status_code == 422


class TestMetalStatus:
    def test_metal_status_is_reachable(self, client):
        response = client.get("/api/inference/metal")

        assert response.status_code == 200
        assert "supported" in response.json()

    def test_an_mlx_model_without_a_launcher_is_refused(self, client, monkeypatch):
        # MLX weights only run on the Apple GPU, which needs the launcher; sending
        # them to the CPU container would fail minutes into a download.
        monkeypatch.setattr(metal, "read_status", lambda: MetalStatus(supported=False))

        response = client.post(
            "/api/inference/text/deploy", json={"model_id": "mlx-community/Qwen2.5-7B"}
        )

        assert response.status_code == 400


class TestDeployValidation:
    def test_a_blank_model_id_is_refused(self, client):
        response = client.post("/api/inference/speech/deploy", json={"model_id": "   "})

        assert response.status_code == 400
