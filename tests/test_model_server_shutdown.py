"""Tests for stopping model server containers when Kayak shuts down.

A speech or text server is a sibling container on the host daemon, so closing the
desktop app -- which removes the Kayak container -- used to leave it running with
its weights still resident and nothing alive to talk to it. Every modality's
container has to be gone by the time the process exits.
"""

import asyncio
from typing import Any, Dict, List, Optional

import pytest

from backend.app.inference import registry
from backend.app.inference.audio_runtimes import SpeechRuntime, TranscriptionRuntime
from backend.app.inference.manager import ServerManager, StatusBroadcaster
from backend.app.inference.vllm_runtime import VLLMRuntime


class FakeContainer:
    def __init__(self, name: str, refuses_stop: bool = False):
        self.name = name
        self.stopped = False
        self.removed = False
        self.stop_timeout: Optional[int] = None
        self._refuses_stop = refuses_stop

    def stop(self, timeout: int = 10) -> None:
        self.stop_timeout = timeout
        if self._refuses_stop:
            raise RuntimeError("refused to stop")
        self.stopped = True

    def remove(self, force: bool = False) -> None:
        self.removed = True


class FakeContainers:
    def __init__(self, containers: Dict[str, FakeContainer]):
        self._containers = containers
        self.lookups: List[str] = []

    def get(self, name: str) -> FakeContainer:
        self.lookups.append(name)
        try:
            return self._containers[name]
        except KeyError:
            raise RuntimeError(f"No such container: {name}")


class FakeClient:
    def __init__(self, containers: Dict[str, FakeContainer]):
        self.containers = FakeContainers(containers)


def build_manager(runtime, containers: Dict[str, FakeContainer]) -> ServerManager:
    """A manager wired to a fake Docker, with no real client created."""
    instance = ServerManager.__new__(ServerManager)
    instance._runtime = runtime
    instance._broadcaster = StatusBroadcaster()
    instance._init_state()
    instance._docker_available = True
    instance._client = FakeClient(containers)
    return instance


class TestShutdownStopsTheContainer:
    def test_the_speech_container_is_stopped_and_removed(self):
        container = FakeContainer("kayak-tts-server")
        manager = build_manager(
            SpeechRuntime(), {"kayak-tts-server": container}
        )

        asyncio.run(manager.shutdown())

        assert container.stopped is True
        assert container.removed is True

    def test_every_modality_stops_its_own_container(self):
        containers = {
            "kayak-vllm-server": FakeContainer("kayak-vllm-server"),
            "kayak-tts-server": FakeContainer("kayak-tts-server"),
            "kayak-stt-server": FakeContainer("kayak-stt-server"),
        }
        for runtime in (VLLMRuntime(), SpeechRuntime(), TranscriptionRuntime()):
            manager = build_manager(runtime, containers)
            asyncio.run(manager.shutdown())

        assert all(container.stopped for container in containers.values())
        assert all(container.removed for container in containers.values())

    def test_a_container_from_a_previous_run_is_stopped_too(self):
        """Nothing was deployed by this process; the container is found by name."""
        container = FakeContainer("kayak-tts-server")
        manager = build_manager(SpeechRuntime(), {"kayak-tts-server": container})
        assert manager._status.container_id is None

        asyncio.run(manager.shutdown())

        assert container.stopped is True

    def test_the_grace_is_short_enough_to_fit_the_daemons_own(self):
        """The whole shutdown races the daemon's timer, seen as low as three seconds."""
        container = FakeContainer("kayak-tts-server")
        manager = build_manager(SpeechRuntime(), {"kayak-tts-server": container})

        asyncio.run(manager.shutdown())

        assert container.stop_timeout is not None
        assert container.stop_timeout <= 3

    def test_a_container_that_refuses_to_stop_is_removed_anyway(self):
        container = FakeContainer("kayak-tts-server", refuses_stop=True)
        manager = build_manager(SpeechRuntime(), {"kayak-tts-server": container})

        asyncio.run(manager.shutdown())

        assert container.stopped is False
        assert container.removed is True

    def test_no_container_of_that_name_is_not_an_error(self):
        manager = build_manager(SpeechRuntime(), {})

        asyncio.run(manager.shutdown())

    def test_docker_being_unavailable_is_not_an_error(self):
        manager = build_manager(SpeechRuntime(), {})
        manager._docker_available = False
        manager._client = None

        asyncio.run(manager.shutdown())

    def test_an_in_flight_deployment_is_superseded(self):
        """A deploy still in a worker thread must not leave a container behind.

        The start path removes what it created as soon as it sees a newer
        generation, so bumping it is what makes the removal happen.
        """
        manager = build_manager(SpeechRuntime(), {})
        generation = manager._deploy_generation

        asyncio.run(manager.shutdown())

        assert manager._deploy_generation > generation
        assert manager._superseded(generation) is True


class TestRegistryShutdown:
    def test_every_manager_is_shut_down(self, monkeypatch):
        stopped: List[str] = []

        class Recording:
            def __init__(self, key: str):
                self._key = key

            async def shutdown(self) -> None:
                await asyncio.sleep(0)
                stopped.append(self._key)

        recorded = [Recording("text"), Recording("speech"), Recording("transcription")]
        monkeypatch.setattr(registry, "managers", lambda: recorded)

        asyncio.run(registry.shutdown())

        assert sorted(stopped) == ["speech", "text", "transcription"]

    def test_one_manager_failing_does_not_strand_the_others(self, monkeypatch):
        stopped: List[str] = []

        class Failing:
            async def shutdown(self) -> None:
                raise RuntimeError("docker went away")

        class Recording:
            async def shutdown(self) -> None:
                stopped.append("ok")

        monkeypatch.setattr(registry, "managers", lambda: [Failing(), Recording()])

        asyncio.run(registry.shutdown())

        assert stopped == ["ok"]
