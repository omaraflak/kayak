"""Tests for stopping sandboxes when Kayak shuts down.

Sandboxes outlive the server on purpose -- an agent's installed packages and
built tooling live in them -- so they are stopped rather than removed, and
opening the conversation again starts them back up. What matters here is that
none are left running.
"""

import asyncio
from typing import Any, Dict, List

import pytest

from backend.app.agent.sandbox import SANDBOX_NAME_PREFIX, SandboxManager


class FakeContainer:
    def __init__(self, name: str, fails: bool = False):
        self.name = name
        self.fails = fails
        self.stopped = False

    def stop(self, timeout: int = 2) -> None:
        if self.fails:
            raise RuntimeError("refused to stop")
        self.stopped = True


class FakeContainers:
    def __init__(self, running: List[FakeContainer]):
        self._running = running
        self.filters_used: Dict[str, Any] = {}

    def list(self, filters: Dict[str, Any]) -> List[FakeContainer]:
        self.filters_used = filters
        return list(self._running)

    def get(self, name: str) -> FakeContainer:
        for container in self._running:
            if container.name == name:
                return container
        raise KeyError(name)


class FakeClient:
    def __init__(self, running: List[FakeContainer]):
        self.containers = FakeContainers(running)


@pytest.fixture
def manager(monkeypatch):
    """A manager wired to a fake Docker, with no real client created."""
    monkeypatch.setattr(SandboxManager, "_init_client", lambda self: None)
    made = SandboxManager()
    made._docker_available = True
    return made


def test_stops_containers_this_process_never_started(manager):
    """After a restart the owned set is empty and the previous run's are running."""
    stray = FakeContainer(f"{SANDBOX_NAME_PREFIX}abc12345")
    manager._client = FakeClient([stray])

    asyncio.run(manager.shutdown_all())

    assert stray.stopped is True


def test_only_looks_at_sandbox_containers(manager):
    """Kayak shares a daemon with everything else on the machine."""
    manager._client = FakeClient([])

    asyncio.run(manager.shutdown_all())

    filters = manager._client.containers.filters_used
    assert filters.get("name") == SANDBOX_NAME_PREFIX
    assert filters.get("status") == "running"


def test_one_stubborn_container_does_not_strand_the_others(manager):
    """Shutdown is already underway; the rest still have to be stopped."""
    stubborn = FakeContainer(f"{SANDBOX_NAME_PREFIX}aaaaaaaa", fails=True)
    ordinary = FakeContainer(f"{SANDBOX_NAME_PREFIX}bbbbbbbb")
    manager._client = FakeClient([stubborn, ordinary])

    asyncio.run(manager.shutdown_all())

    assert ordinary.stopped is True


def test_does_nothing_without_docker(manager):
    """Kayak runs with no Docker at all; shutdown must not raise."""
    manager._docker_available = False
    manager._client = None

    asyncio.run(manager.shutdown_all())
