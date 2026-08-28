"""Tests for the modality-scoped serving API.

The routes carry a modality now, which introduces a way to get the URL space wrong:
`/{modality}/stop` has the same shape as `/metal/stop`, and FastAPI matches in
declaration order. Getting that order wrong turns a working Metal control into a
422 about an unknown modality, which no client would think to look for.
"""

import pytest
from fastapi.testclient import TestClient

from backend.app.inference import metal, registry
from backend.app.inference.models import MetalStatus, Modality
from backend.app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def inert_controls(monkeypatch) -> None:
    """Neuters the controls that would act on real servers.

    These routes are exercised for their routing, not their effect. Left live, a
    plain `pytest` run stops whatever model the developer has running -- which is
    exactly what happened while this file was being written, and is invisible
    because the suite still passes.
    """

    async def no_stop() -> None:
        return None

    monkeypatch.setattr(registry.text_manager, "stop_server", no_stop)
    # An actual Metal start would deploy a model on a machine that supports it.
    monkeypatch.setattr(metal, "read_status", lambda: MetalStatus(supported=False))


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

    def test_servers_do_not_share_a_port(self, client):
        payload = client.get("/api/inference/status").json()
        ports = [status["port"] for status in payload.values()]

        assert len(set(ports)) == len(ports)

    def test_an_unknown_modality_is_rejected(self, client):
        response = client.get("/api/inference/interpretive-dance/status")

        assert response.status_code == 422


class TestMetalRoutesAreNotShadowed:
    """`/metal/...` must reach the Metal handlers, not the modality ones."""

    def test_metal_status_is_reachable(self, client):
        response = client.get("/api/inference/metal")

        assert response.status_code == 200
        assert "supported" in response.json()

    def test_metal_stop_is_not_read_as_a_modality(self, client, inert_controls):
        response = client.post("/api/inference/metal/stop")

        # 422 here would mean `/{modality}/stop` swallowed it and rejected "metal".
        assert response.status_code == 200
        assert "supported" in response.json()

    def test_metal_start_is_not_read_as_a_modality(self, client, inert_controls):
        response = client.post(
            "/api/inference/metal/start", json={"model_id": "mlx-community/Qwen2.5-7B"}
        )

        # 409 is the honest answer with no launcher present; the point is that the
        # request reached the Metal handler rather than the modality one, which
        # would have rejected "metal" with a 422.
        assert response.status_code == 409


class TestDeployValidation:
    def test_a_blank_model_id_is_refused(self, client):
        response = client.post("/api/inference/speech/deploy", json={"model_id": "   "})

        assert response.status_code == 400
