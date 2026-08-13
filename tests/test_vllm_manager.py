import asyncio
from typing import Any, Dict, Optional

import pytest

from backend.app.vllm.manager import VLLMManager
from backend.app.vllm.models import VLLMDeploymentProgress, VLLMServerState


class FakeContainer:
    """Stands in for a docker-py container object."""

    def __init__(self, status: str, exit_code: int = 0, oom_killed: bool = False):
        self.id = "c" * 64
        self.status = status
        self.attrs: Dict[str, Any] = {
            "State": {"ExitCode": exit_code, "OOMKilled": oom_killed},
            "Config": {"Cmd": ["--model", "Org/Model"]},
        }


class FakeContainers:
    def __init__(self, container: Optional[FakeContainer]):
        self._container = container

    def get(self, _name: str) -> FakeContainer:
        if self._container is None:
            raise RuntimeError("No such container")
        return self._container


class FakeDockerClient:
    def __init__(self, container: Optional[FakeContainer]):
        self.containers = FakeContainers(container)


class UnreachableClient:
    """An httpx.AsyncClient whose every request fails, i.e. nothing is serving."""

    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, *_args, **_kwargs):
        raise ConnectionError("nothing listening")


@pytest.fixture
def manager(monkeypatch) -> VLLMManager:
    """A manager with Docker stubbed out and no live endpoint."""
    monkeypatch.setattr("backend.app.vllm.manager.httpx.AsyncClient", UnreachableClient)

    instance = VLLMManager.__new__(VLLMManager)
    instance._client = None
    instance._docker_available = True
    instance._status = VLLMDeploymentProgress()
    instance._log_history = []
    instance._listeners = set()
    instance._monitor_task = None
    instance._log_stop_event = None
    return instance


class TestCrashedContainerIsReported:
    """
    A vLLM container that dies during startup -- which is what running out of memory
    looks like -- used to leave the status on 'loading' forever: the endpoint never
    answers, and no branch handled a container that exists but is not running.
    """

    def test_a_container_that_exited_while_loading_becomes_an_error(self, manager):
        manager._client = FakeDockerClient(FakeContainer("exited", exit_code=1))
        manager._status.state = VLLMServerState.LOADING
        manager._status.model_id = "Org/Model"

        status = asyncio.run(manager.check_and_sync_status())

        assert status.state == VLLMServerState.ERROR
        assert status.exit_code == 1
        assert "exited with code 1" in (status.error or "")

    def test_an_out_of_memory_kill_says_so(self, manager):
        manager._client = FakeDockerClient(
            FakeContainer("exited", exit_code=137, oom_killed=True)
        )
        manager._status.state = VLLMServerState.STARTING_CONTAINER

        status = asyncio.run(manager.check_and_sync_status())

        assert status.state == VLLMServerState.ERROR
        # The remedy belongs in the message; the exit code alone explains nothing.
        assert "memory" in (status.error or "").lower()
        assert "max-model-len" in (status.error or "")

    def test_a_running_container_still_reports_as_initializing(self, manager):
        manager._client = FakeDockerClient(FakeContainer("running"))
        manager._status.state = VLLMServerState.LOADING

        status = asyncio.run(manager.check_and_sync_status())

        assert status.state == VLLMServerState.LOADING

    def test_an_idle_manager_with_no_container_stays_idle(self, manager):
        manager._client = FakeDockerClient(None)

        status = asyncio.run(manager.check_and_sync_status())

        assert status.state == VLLMServerState.IDLE

    def test_a_ready_server_that_stops_answering_is_reported_stopped(self, manager):
        manager._client = FakeDockerClient(None)
        manager._status.state = VLLMServerState.READY
        manager._status.model_id = "Org/Model"

        status = asyncio.run(manager.check_and_sync_status())

        assert status.state == VLLMServerState.STOPPED

    def test_a_serving_model_that_crashes_reports_why(self, manager):
        # A long context can exhaust memory well after the server came up healthy.
        manager._client = FakeDockerClient(
            FakeContainer("exited", exit_code=137, oom_killed=True)
        )
        manager._status.state = VLLMServerState.READY
        manager._status.model_id = "Org/Model"

        status = asyncio.run(manager.check_and_sync_status())

        assert status.state == VLLMServerState.ERROR
        assert "memory" in (status.error or "").lower()

    def test_an_orderly_shutdown_is_not_an_error(self, manager):
        manager._client = FakeDockerClient(FakeContainer("exited", exit_code=0))
        manager._status.state = VLLMServerState.READY

        status = asyncio.run(manager.check_and_sync_status())

        assert status.state == VLLMServerState.STOPPED


class TestServingGuard:
    def test_weights_in_use_are_recognized(self, manager):
        manager._status.model_id = "Org/Model"
        manager._status.state = VLLMServerState.READY

        assert manager.is_serving("Org/Model") is True
        assert manager.is_serving("Org/Other") is False

    def test_a_stopped_server_does_not_hold_its_weights(self, manager):
        manager._status.model_id = "Org/Model"
        manager._status.state = VLLMServerState.STOPPED

        assert manager.is_serving("Org/Model") is False
