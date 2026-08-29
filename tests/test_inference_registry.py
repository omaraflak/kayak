"""Tests for running more than one local server at a time.

There used to be one manager and therefore one container, so starting any model
stopped whatever was running. A voice model is only useful *alongside* a text model,
so the important properties here are all about independence: separate containers,
separate ports, separate state, and one event stream on which each frame says which
server it belongs to.
"""

import asyncio

import pytest

from backend.app.inference import registry
from backend.app.inference.manager import ServerManager, StatusBroadcaster
from backend.app.inference.models import DeployRequest, Modality, ServerState
from backend.app.inference.runtimes import SpecContext
from backend.app.inference.audio_runtimes import SpeechRuntime
from backend.app.inference.vllm_runtime import VLLMRuntime


async def _false() -> bool:
    """Stands in for a Docker probe on a machine with no daemon."""
    return False


def _detached(runtime, broadcaster=None) -> ServerManager:
    """A manager with its runtime wired up but no Docker probing."""
    instance = ServerManager.__new__(ServerManager)
    instance._runtime = runtime
    instance._broadcaster = broadcaster or StatusBroadcaster()
    instance._init_state()
    return instance


class TestServersAreIndependent:
    def test_every_modality_has_its_own_manager(self):
        modalities = {manager.modality for manager in registry.managers()}

        assert Modality.TEXT in modalities
        assert Modality.SPEECH in modalities

    def test_containers_and_ports_never_collide(self):
        names = [manager.container_name for manager in registry.managers()]
        ports = [manager.runtime.default_port for manager in registry.managers()]

        # A shared container name would make each start force-remove the other's
        # container; a shared port would make the second server unstartable.
        assert len(set(names)) == len(names)
        assert len(set(ports)) == len(ports)

    def test_one_servers_state_does_not_leak_into_another(self):
        text = _detached(VLLMRuntime())
        speech = _detached(SpeechRuntime())

        text._status.model_id = "Org/TextModel"
        text._status.state = ServerState.READY

        assert speech._status.model_id is None
        assert speech._status.state == ServerState.IDLE
        assert speech.is_serving("Org/TextModel") is False

    def test_each_status_names_its_own_modality(self):
        assert _detached(VLLMRuntime())._status.modality == Modality.TEXT
        assert _detached(SpeechRuntime())._status.modality == Modality.SPEECH

    def test_deploying_does_not_relabel_the_server(self, monkeypatch):
        """A deploy rebuilds the status object from scratch.

        The modality field has a default, so omitting it there is silent: the
        speech server would come up broadcasting itself as the text one, and every
        client would render its startup under the wrong model.
        """
        speech = _detached(SpeechRuntime())
        # Fail the deployment immediately; only the status it publishes matters.
        monkeypatch.setattr(speech, "_ensure_docker", _false)

        status = asyncio.run(speech.deploy_model(DeployRequest(model_id="hexgrad/Kokoro-82M")))

        assert status.modality == Modality.SPEECH


class TestOneStreamCarriesEveryServer:
    def test_events_are_stamped_with_the_server_they_describe(self):
        broadcaster = StatusBroadcaster()
        text = _detached(VLLMRuntime(), broadcaster)
        speech = _detached(SpeechRuntime(), broadcaster)

        async def scenario():
            queue = broadcaster.subscribe([])
            text._add_log("loading weights")
            speech._add_log("loading voices")
            return [queue.get_nowait(), queue.get_nowait()]

        first, second = asyncio.run(scenario())

        # Without the stamp, a voice model's startup logs would render under the
        # text model and its "ready" would mark the wrong server online.
        assert first == {"modality": "text", "type": "log", "line": "loading weights"}
        assert second == {"modality": "speech", "type": "log", "line": "loading voices"}

    def test_a_new_subscriber_is_greeted_with_every_server(self):
        async def scenario():
            queue = registry.subscribe()
            greeting = []
            while not queue.empty():
                greeting.append(queue.get_nowait())
            registry.unsubscribe(queue)
            return greeting

        greeting = asyncio.run(scenario())

        # A tab that reconnects must learn the state of every server, not just one.
        modalities = {event["data"]["modality"] for event in greeting}
        assert modalities == {m.value for m in Modality}
        assert all(event["type"] == "status" for event in greeting)
        # Stamped like every later frame, so a client handles one shape, not two.
        assert {event["modality"] for event in greeting} == modalities


class TestWeightsInUseByAnyServer:
    def test_deleting_weights_checks_every_server(self, monkeypatch):
        speech = registry.manager_for(Modality.SPEECH)
        monkeypatch.setattr(speech._status, "model_id", "hexgrad/Kokoro-82M")
        monkeypatch.setattr(speech._status, "state", ServerState.READY)

        # Checking only the text manager -- which was enough when there was one --
        # would delete these weights out from under a running speech server.
        assert registry.is_serving("hexgrad/Kokoro-82M") is True
        assert registry.is_serving("Org/SomethingElse") is False


class TestRuntimeDispatch:
    """Which repositories a runtime claims, from Hub metadata alone."""

    def test_kokoro_is_matched_without_a_library(self):
        # The Hub reports no library_name at all for the most downloaded TTS model,
        # so matching on library alone would exclude it.
        assert SpeechRuntime().can_serve("hexgrad/Kokoro-82M", library_name=None) is True

    def test_transformers_speech_models_are_matched_by_library(self):
        runtime = SpeechRuntime()

        assert runtime.can_serve("microsoft/VibeVoice-1.5B", library_name="transformers")
        assert runtime.can_serve("facebook/mms-tts-eng", library_name="transformers")

    def test_a_backend_we_do_not_ship_is_not_claimed(self):
        # Offering a Start button that cannot work is worse than saying so upfront.
        assert SpeechRuntime().can_serve("coqui/XTTS-v2", library_name="coqui") is False
        assert SpeechRuntime().can_serve("SWivid/F5-TTS", library_name="f5-tts") is False

    def test_vllm_claims_anything_with_its_pipeline_tag(self):
        assert VLLMRuntime().can_serve("Qwen/Qwen2.5-7B-Instruct", library_name="transformers")
        assert VLLMRuntime().can_serve("some/unusual-repo", library_name=None)


class TestSpeechContainerSpec:
    @pytest.fixture
    def spec(self):
        return asyncio.run(
            SpeechRuntime().container_spec(
                DeployRequest(model_id="hexgrad/Kokoro-82M"),
                SpecContext(has_gpu=False, docker_memory_bytes=None, hf_token=None),
            )
        )

    def test_the_model_is_an_argument_not_part_of_the_image(self, spec):
        # The image is a runtime. Baking a model into it would make "pick any model"
        # impossible, which is the whole point of dispatching on the repository.
        assert "--model" in spec.command
        assert spec.command[spec.command.index("--model") + 1] == "hexgrad/Kokoro-82M"

    def test_no_text_generation_flags_leak_in(self, spec):
        # A speech model has no KV cache, context window or tool-call parser.
        for flag in ("--max-model-len", "--tool-call-parser", "--gpu-memory-utilization"):
            assert flag not in spec.command
        assert "VLLM_CPU_KVCACHE_SPACE" not in spec.environment

    def test_the_token_is_only_passed_when_there_is_one(self, spec):
        assert "HF_TOKEN" not in spec.environment

        with_token = asyncio.run(
            SpeechRuntime().container_spec(
                DeployRequest(model_id="hexgrad/Kokoro-82M"),
                SpecContext(has_gpu=False, docker_memory_bytes=None, hf_token="hf_abc"),
            )
        )
        assert with_token.environment["HF_TOKEN"] == "hf_abc"
