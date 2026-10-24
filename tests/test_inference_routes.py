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


class TestMetalRefusalIsASentence:
    def test_the_refusal_says_what_to_do(self, client, monkeypatch):
        monkeypatch.setattr(metal, "read_status", lambda: MetalStatus(supported=False))

        response = client.post(
            "/api/inference/text/deploy", json={"model_id": "mlx-community/Qwen2.5-7B"}
        )

        detail = response.json()["detail"]
        assert "Apple chips" in detail
        assert "Metal" not in detail and "launcher" not in detail

    def test_the_launchers_own_reason_wins_when_it_is_readable(self, client, monkeypatch):
        monkeypatch.setattr(
            metal, "read_status", lambda: MetalStatus(supported=False, detail="Running under Rosetta.")
        )

        response = client.post(
            "/api/inference/text/deploy", json={"model_id": "mlx-community/Qwen2.5-7B"}
        )

        assert response.json()["detail"] == "Running under Rosetta."


class TestDeletingWeights:
    def test_a_running_model_is_not_deleted(self, client, monkeypatch):
        from backend.app.inference import registry

        monkeypatch.setattr(registry, "is_serving", lambda repo_id: True)

        response = client.delete("/api/inference/cache/Org/Model")

        assert response.status_code == 409
        assert response.json()["detail"] == "This model is running. Stop it before deleting it."

    def test_a_failed_delete_keeps_its_reason_out_of_sight(self, client, monkeypatch):
        from backend.app.inference import registry

        async def refuse(repo_id):
            raise PermissionError("[Errno 13] Permission denied: '/app/data/huggingface_cache'")

        monkeypatch.setattr(registry, "is_serving", lambda repo_id: False)
        monkeypatch.setattr(registry, "delete_cached_model", refuse)

        response = client.delete("/api/inference/cache/Org/Model")

        assert response.status_code == 500
        assert response.json()["detail"] == "Kayak couldn't delete this model's files. Try again."


class TestClassifyingBeforeTheDownload:
    """A gated or missing model used to fail only after gigabytes had downloaded."""

    @staticmethod
    def classify(monkeypatch, handler, token=""):
        import asyncio

        import httpx

        from backend.app.inference import routes

        real_client = httpx.AsyncClient

        def client_with(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_client(*args, **kwargs)

        monkeypatch.setattr(routes.httpx, "AsyncClient", client_with)
        monkeypatch.setattr(routes.settings, "HUGGINGFACE_API_KEY", token)
        return asyncio.run(routes.classify_model("Org/Model"))

    def test_a_missing_model_is_named_as_missing(self, monkeypatch):
        import httpx

        result = self.classify(monkeypatch, lambda request: httpx.Response(404))

        assert result.not_found is True
        assert "couldn't find this model" in result.message

    def test_a_gated_model_without_a_token_asks_for_one(self, monkeypatch):
        import httpx
        from backend.app.inference import failures

        payload = {"pipeline_tag": "text-generation", "gated": "manual"}
        result = self.classify(monkeypatch, lambda request: httpx.Response(200, json=payload))

        assert result.gated is True
        assert result.needs_token is True
        assert result.message == failures.GATED_NEEDS_TOKEN

    def test_a_gated_model_this_account_cannot_get_says_so(self, monkeypatch):
        import httpx
        from backend.app.inference import failures

        def handler(request):
            if request.method == "HEAD":
                return httpx.Response(403)
            return httpx.Response(200, json={"pipeline_tag": "text-generation", "gated": "auto"})

        result = self.classify(monkeypatch, handler, token="hf_x")

        assert result.message == failures.GATED_NO_ACCESS
        assert result.needs_token is False

    def test_a_gated_model_this_account_can_get_starts(self, monkeypatch):
        import httpx

        def handler(request):
            if request.method == "HEAD":
                return httpx.Response(302)
            return httpx.Response(200, json={"pipeline_tag": "text-generation", "gated": "auto"})

        result = self.classify(monkeypatch, handler, token="hf_x")

        assert result.gated is True
        assert result.message is None
        assert result.modality == Modality.TEXT

    def test_a_refused_token_is_named(self, monkeypatch):
        import httpx
        from backend.app.inference import failures

        result = self.classify(monkeypatch, lambda request: httpx.Response(401), token="hf_bad")

        assert result.needs_token is True
        assert result.message == failures.TOKEN_REJECTED

    def test_an_unreachable_hub_does_not_block_a_start(self, monkeypatch):
        import httpx

        def handler(request):
            raise httpx.ConnectError("offline")

        result = self.classify(monkeypatch, handler)

        assert result.unknown is True
        assert result.message is None


class TestSearchFailuresAreSentences:
    @staticmethod
    def search(monkeypatch, handler):
        import httpx

        from backend.app.routes import models as model_routes

        real_client = httpx.AsyncClient

        def client_with(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_client(*args, **kwargs)

        monkeypatch.setattr(model_routes.httpx, "AsyncClient", client_with)
        return TestClient(app).get("/api/models/huggingface/search?query=qwen")

    @pytest.mark.parametrize(
        "status, expected",
        [
            (401, "Hugging Face didn't accept your key. Check it in Platform Settings."),
            (429, "Hugging Face is limiting searches from this Mac."),
            (503, "Hugging Face isn't answering properly right now. Try again in a few minutes."),
        ],
    )
    def test_upstream_answers(self, monkeypatch, status, expected):
        import httpx

        response = self.search(monkeypatch, lambda request: httpx.Response(status))

        assert response.json()["detail"].startswith(expected)
        assert str(status) not in response.json()["detail"]

    def test_unreachable(self, monkeypatch):
        import httpx

        def handler(request):
            raise httpx.ConnectError("[Errno 8] nodename nor servname provided")

        response = self.search(monkeypatch, handler)

        assert response.json()["detail"] == (
            "Kayak couldn't reach Hugging Face. Check your internet connection, then try again."
        )


class TestGgufOnlyRepositories:
    """A repository of GGUF files is tagged like any text model, but cannot be run."""

    def test_gguf_files_without_a_config_cannot_be_run(self):
        from backend.app.inference.routes import only_gguf_weights

        assert only_gguf_weights(
            [{"rfilename": "README.md"}, {"rfilename": "model-q4_k_m.gguf"}]
        )

    def test_a_repository_that_also_ships_a_config_can(self):
        from backend.app.inference.routes import only_gguf_weights

        assert not only_gguf_weights(
            [{"rfilename": "config.json"}, {"rfilename": "model-q4_k_m.gguf"}]
        )

    def test_an_ordinary_repository_or_a_missing_list_is_left_alone(self):
        from backend.app.inference.routes import only_gguf_weights

        assert not only_gguf_weights(
            [{"rfilename": "config.json"}, {"rfilename": "model.safetensors"}]
        )
        assert not only_gguf_weights(None)

