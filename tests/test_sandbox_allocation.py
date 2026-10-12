"""Tests for how much memory and CPU each sandbox gets.

Every sandbox used to be capped at a fixed 2 GB and one core, whatever the Mac had,
and agents doing real work were killed for it. The default is now a share of what
Docker Desktop's VM has, and the user can choose otherwise in Settings.
"""

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.agent import sandbox as sandbox_module
from backend.app.agent.sandbox import (
    Allocation,
    SandboxManager,
    _limits,
    recommended_allocation,
    resolve_allocation,
)
from backend.app.config import Settings, settings

GIB = 1024 ** 3


class TestRecommendation:
    def test_half_the_memory_and_every_core(self):
        assert recommended_allocation(16 * GIB, 10) == Allocation(memory_gb=8, cpus=10)

    def test_a_small_vm_still_gets_a_gigabyte(self):
        assert recommended_allocation(int(1.5 * GIB), 2).memory_gb == 1

    def test_nothing_is_recommended_when_docker_is_silent(self):
        assert recommended_allocation(None, None) is None
        assert recommended_allocation(8 * GIB, None) is None


class TestResolution:
    def test_unset_choices_follow_the_recommendation(self):
        assert resolve_allocation(None, None, 8 * GIB, 4) == Allocation(memory_gb=4, cpus=4)

    def test_a_choice_wins(self):
        assert resolve_allocation(6, 2, 8 * GIB, 4) == Allocation(memory_gb=6, cpus=2)

    def test_a_choice_larger_than_the_vm_is_clamped(self):
        # Docker Desktop's memory was lowered after the choice was made.
        resolved = resolve_allocation(32, 16, 8 * GIB, 4)
        assert resolved.memory_gb == 8
        assert resolved.cpus == 4

    def test_one_choice_alone_leaves_the_other_recommended(self):
        assert resolve_allocation(None, 1, 8 * GIB, 4) == Allocation(memory_gb=4, cpus=1)


class TestDockerLimits:
    def test_memory_and_cpu_become_docker_limits(self):
        limits = _limits(Allocation(memory_gb=3, cpus=2.5))

        assert limits["mem_limit"] == 3 * GIB
        # Docker refuses a memory limit above the swap limit.
        assert limits["memswap_limit"] >= limits["mem_limit"]
        assert limits["cpu_quota"] == 250_000
        assert limits["cpu_period"] == 100_000


class FakeContainer:
    def __init__(self, memory=2 * GIB, quota=100_000, status="running"):
        self.name = "kayak-sandbox-abc"
        self.id = "id-abc"
        self.status = status
        self.attrs = {"HostConfig": {"Memory": memory, "CpuQuota": quota}}
        self.updates = []

    def update(self, **kwargs):
        self.updates.append(kwargs)

    def start(self):
        self.status = "running"


class FakeImages:
    def get(self, _name):
        return object()


class FakeContainers:
    def __init__(self, existing=None):
        self.existing = existing
        self.run_kwargs = None

    def get(self, _key):
        if self.existing is None:
            raise sandbox_module.NotFound("missing")
        return self.existing

    def run(self, **kwargs):
        self.run_kwargs = kwargs
        return FakeContainer()


class FakeClient:
    def __init__(self, existing=None):
        self.containers = FakeContainers(existing)
        self.images = FakeImages()

    def info(self):
        return {"MemTotal": 8 * GIB, "NCPU": 4}


@pytest.fixture
def manager(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(SandboxManager, "_init_client", lambda self: None)
    monkeypatch.setattr(sandbox_module.docker_host, "host_path", lambda path: str(path))
    monkeypatch.setattr(settings, "SANDBOX_MEMORY_GB", None)
    monkeypatch.setattr(settings, "SANDBOX_CPUS", None)
    made = SandboxManager()
    made._docker_available = True
    return made


class TestSandboxesGetTheAllocation:
    def test_a_new_sandbox_is_created_with_the_recommendation(self, manager, tmp_path):
        manager._client = FakeClient()

        asyncio.run(manager.create_sandbox("conv-1", tmp_path))

        run = manager._client.containers.run_kwargs
        assert run["mem_limit"] == 4 * GIB
        assert run["cpu_quota"] == 4 * 100_000

    def test_an_existing_sandbox_is_brought_up_to_date(self, manager, monkeypatch):
        monkeypatch.setattr(settings, "SANDBOX_MEMORY_GB", 6)
        container = FakeContainer(memory=2 * GIB, quota=100_000, status="exited")
        manager._client = FakeClient(existing=container)

        assert asyncio.run(manager.ensure_running("id-abc")) is True
        assert container.updates == [_limits(Allocation(memory_gb=6, cpus=4))]
        assert container.status == "running"

    def test_a_sandbox_already_matching_is_left_alone(self, manager):
        container = FakeContainer(memory=4 * GIB, quota=400_000)
        manager._client = FakeClient(existing=container)

        asyncio.run(manager.ensure_running("id-abc"))

        assert container.updates == []


class TestStoredAllocation:
    def test_a_choice_survives_a_restart_and_can_be_reset(self, tmp_path: Path, monkeypatch):
        stored = Settings()
        monkeypatch.setattr(stored, "SETTINGS_FILE", tmp_path / "settings.json")

        stored.save_settings({"SANDBOX_MEMORY_GB": 6, "SANDBOX_CPUS": 2})
        assert json.loads((tmp_path / "settings.json").read_text())["SANDBOX_MEMORY_GB"] == 6

        stored.save_settings({"SANDBOX_MEMORY_GB": None, "SANDBOX_CPUS": None})
        reloaded = Settings()
        monkeypatch.setattr(reloaded, "SETTINGS_FILE", tmp_path / "settings.json")
        reloaded.load_from_file()
        assert reloaded.SANDBOX_MEMORY_GB is None

    def test_a_none_credential_is_still_left_alone(self, tmp_path: Path, monkeypatch):
        stored = Settings()
        monkeypatch.setattr(stored, "SETTINGS_FILE", tmp_path / "settings.json")
        stored.save_settings({"OPENAI_API_KEY": "sk-keep"})

        stored.save_settings({"OPENAI_API_KEY": None})

        assert stored.OPENAI_API_KEY == "sk-keep"

    def test_a_nonsense_value_on_disk_falls_back_to_the_recommendation(self, tmp_path, monkeypatch):
        (tmp_path / "settings.json").write_text(json.dumps({"SANDBOX_MEMORY_GB": "lots", "SANDBOX_CPUS": -1}))
        stored = Settings()
        monkeypatch.setattr(stored, "SETTINGS_FILE", tmp_path / "settings.json")
        stored.load_from_file()

        assert stored.SANDBOX_MEMORY_GB is None
        assert stored.SANDBOX_CPUS is None


class TestSettingsApi:
    @pytest.fixture
    def client(self, monkeypatch, tmp_path: Path):
        from backend.app.main import app
        from backend.app.routes import settings as settings_route

        monkeypatch.setattr(settings, "SETTINGS_FILE", tmp_path / "settings.json")
        monkeypatch.setattr(settings, "SANDBOX_MEMORY_GB", None)
        monkeypatch.setattr(settings, "SANDBOX_CPUS", None)
        # Saved in place on the shared settings; restored so no test sees another's key.
        monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
        monkeypatch.setattr(settings_route.sandbox_manager, "client", lambda: FakeClient())
        return TestClient(app)

    def test_the_page_is_told_what_docker_has_and_what_is_recommended(self, client):
        sandbox = client.get("/api/settings").json()["sandbox"]

        assert sandbox == {
            "memory_gb": None,
            "cpus": None,
            "recommended_memory_gb": 4,
            "recommended_cpus": 4,
            "total_memory_gb": 8.0,
            "total_cpus": 4,
        }

    def test_a_chosen_allocation_is_saved_and_reported(self, client):
        response = client.post("/api/settings", json={"sandbox": {"memory_gb": 6, "cpus": 2}})

        assert response.status_code == 200
        assert response.json()["settings"]["sandbox"]["memory_gb"] == 6
        assert settings.SANDBOX_CPUS == 2

    def test_saving_credentials_leaves_the_allocation_alone(self, client):
        client.post("/api/settings", json={"sandbox": {"memory_gb": 6, "cpus": 2}})

        client.post("/api/settings", json={"OPENAI_API_KEY": "sk-new"})

        assert settings.SANDBOX_MEMORY_GB == 6

    def test_a_zero_allocation_is_refused(self, client):
        response = client.post("/api/settings", json={"sandbox": {"memory_gb": 0, "cpus": 2}})

        assert response.status_code == 422
