"""Tests for stopping sandboxes when Kayak shuts down.

Sandboxes outlive the server on purpose -- an agent's installed packages and
built tooling live in them -- so they are stopped rather than removed, and
opening the conversation again starts them back up. What matters here is that
none are left running.
"""

import asyncio
from datetime import datetime, timezone
import threading
import time
from typing import Any, Dict, List

import pytest

from backend.app.agent.sandbox import SANDBOX_NAME_PREFIX, SandboxManager


class FakeContainer:
    def __init__(self, name: str, fails: bool = False, delay: float = 0.0):
        self.name = name
        # The Docker SDK identifies containers by id; the name is only a label.
        self.id = f"id-{name}"
        self.fails = fails
        self.delay = delay
        self.stopped = False

    def stop(self, timeout: int = 2) -> None:
        if self.delay:
            time.sleep(self.delay)
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

    def get(self, key: str) -> FakeContainer:
        for container in self._running:
            if key in (container.id, container.name):
                return container
        raise KeyError(key)


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


def test_stops_sandboxes_at_the_same_time(manager):
    """The grace period before the daemon kills the server is a hard budget.

    Docker Desktop has been seen allowing as little as three seconds, so stopping
    in sequence ran out of time on an install with several sandboxes and left the
    ones it had not reached still running.
    """
    slow = [
        FakeContainer(f"{SANDBOX_NAME_PREFIX}0000000{index}", delay=0.2)
        for index in range(5)
    ]
    manager._client = FakeClient(slow)

    started = time.monotonic()
    asyncio.run(manager.shutdown_all())
    elapsed = time.monotonic() - started

    assert all(container.stopped for container in slow)
    assert elapsed < 0.5, "sandboxes were stopped one after another"


def test_stops_an_owned_container_only_once(manager):
    """A sandbox this process started is also in the running list."""
    owned = FakeContainer(f"{SANDBOX_NAME_PREFIX}dddddddd")
    manager._client = FakeClient([owned])
    manager._owned_containers.add(owned.id)

    stops = []
    original = owned.stop
    owned.stop = lambda timeout=2: (stops.append(threading.current_thread()), original())

    asyncio.run(manager.shutdown_all())

    assert len(stops) == 1


def test_opening_a_conversation_starts_its_sandbox_again(monkeypatch):
    """The other half of the contract: stopped at shutdown, back on next use."""
    from backend.app.models import Conversation
    from backend.app.routes import conversations as routes

    revived: List[str] = []

    async def fake_ensure_running(container_id: str) -> bool:
        revived.append(container_id)
        return True

    monkeypatch.setattr(
        routes.sandbox_manager, "ensure_running", fake_ensure_running
    )

    now = datetime.now(timezone.utc).isoformat()

    def conversation(**fields: Any) -> Conversation:
        return Conversation(
            id="c", title="t", agent_id="a", created_at=now, updated_at=now, **fields
        )

    async def exercise() -> None:
        routes._revive_sandbox(conversation(container_id="box-1"))
        # Nothing to revive: no container of its own, and a sub-agent shares its
        # parent's rather than owning one.
        routes._revive_sandbox(conversation())
        routes._revive_sandbox(
            conversation(container_id="box-1", parent_conversation_id="c1")
        )
        await asyncio.sleep(0)

    asyncio.run(exercise())

    assert revived == ["box-1"]
