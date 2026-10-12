"""Regression tests for bugs fixed while simplifying Kayak for macOS."""

import asyncio
from pathlib import Path

import pytest

from backend.app.config import settings


class TestSkillFrontmatter:
    def test_a_description_with_a_colon_survives_a_round_trip(self, tmp_path: Path, monkeypatch):
        # Written bare, "Use when: ..." is not valid YAML, and the skill came back
        # with "---" as its description and the header inside its instructions.
        from backend.app.skills.registry import SkillRegistry

        monkeypatch.setattr(settings, "SKILLS_DIR", tmp_path)
        registry = SkillRegistry()
        registry.save_skill("planner", "Use when: planning a trip", "Plan carefully.")

        reloaded = SkillRegistry()
        skill = reloaded.get_skill("planner")

        assert skill.description == "Use when: planning a trip"
        assert skill.instructions == "Plan carefully."


class TestExposure:
    """Inside Docker, Kayak binds 0.0.0.0 but is published on 127.0.0.1 only."""

    def test_a_container_published_on_loopback_is_not_exposed(self, monkeypatch):
        from backend.app.routes import settings as settings_route

        monkeypatch.setattr(settings_route.docker_host, "published_hosts", lambda port: ["127.0.0.1"])
        monkeypatch.setattr(settings, "HOST", "0.0.0.0")

        assert settings_route.reachable_address() == "127.0.0.1"
        assert settings_route._security_posture().warning is None

    def test_a_container_published_on_every_interface_is_exposed(self, monkeypatch):
        from backend.app.routes import settings as settings_route

        monkeypatch.setattr(settings_route.docker_host, "published_hosts", lambda port: ["0.0.0.0"])
        monkeypatch.setattr(settings, "AUTH_TOKEN", "")

        posture = settings_route._security_posture()
        assert posture.is_loopback is False
        assert "no access token" in posture.warning

    def test_outside_a_container_the_bind_address_decides(self, monkeypatch):
        from backend.app.routes import settings as settings_route

        monkeypatch.setattr(settings_route.docker_host, "published_hosts", lambda port: None)
        monkeypatch.setattr(settings, "HOST", "192.168.1.20")

        assert settings_route.reachable_address() == "192.168.1.20"


class TestSandboxKills:
    def test_a_kill_after_the_deadline_is_a_timeout(self):
        import time
        from backend.app.agent.sandbox import _killed_message

        message = _killed_message(137, started=time.monotonic() - 61, timeout=60)
        assert "Timed out after 60 seconds" in message

    def test_a_kill_before_the_deadline_is_memory(self):
        import time
        from backend.app.agent.sandbox import _killed_message

        message = _killed_message(137, started=time.monotonic(), timeout=60)
        assert "memory" in message

    def test_other_exit_codes_are_not_kills(self):
        import time
        from backend.app.agent.sandbox import _killed_message

        assert _killed_message(1, started=time.monotonic(), timeout=60) is None


class TestCustomToolsRunInTheSandbox:
    SOURCE = 'def shout(text: str) -> str:\n    """Shouts."""\n    return text.upper()\n'

    @pytest.fixture
    def installed(self, tmp_path: Path, monkeypatch):
        from backend.app.tools.registry import tool_registry

        (tmp_path / "shout").mkdir()
        (tmp_path / "shout" / "tool.py").write_text(self.SOURCE)
        monkeypatch.setattr(settings, "TOOLS_DIR", tmp_path)
        tool_registry.load_custom_tools()
        yield tool_registry
        monkeypatch.undo()
        tool_registry.load_custom_tools()

    def test_without_a_container_nothing_runs(self, installed):
        output = asyncio.run(installed.execute_tool("shout", {"text": "hi"}, {}))
        assert output.startswith("Error:")

    def test_the_entrypoint_read_from_source_is_what_the_sandbox_calls(self, installed, monkeypatch):
        from backend.app.agent import sandbox

        calls = {}

        async def fake_exec(container_id, entrypoint, tool_code, arguments, timeout=60):
            calls.update(container_id=container_id, entrypoint=entrypoint, arguments=arguments)
            return "HI"

        monkeypatch.setattr(sandbox.sandbox_manager, "exec_custom_tool", fake_exec)

        output = asyncio.run(installed.execute_tool("shout", {"text": "hi"}, {"container_id": "c1"}))

        assert output == "HI"
        assert calls == {"container_id": "c1", "entrypoint": "shout", "arguments": {"text": "hi"}}

    def test_the_runner_script_calls_the_entrypoint(self, tmp_path: Path):
        import subprocess
        import sys
        from backend.app.agent.sandbox import _build_custom_tool_runner_script

        script = _build_custom_tool_runner_script("shout", self.SOURCE, {"text": "hi"})
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)

        assert result.stdout.strip() == "HI"


class TestAudioServerRequestsAreSerialised:
    def test_two_requests_to_one_server_never_overlap(self, monkeypatch):
        from backend.app.audio import server
        from backend.app.inference.models import Modality

        monkeypatch.setattr(server, "server_base", lambda modality: "http://audio")
        active = {"now": 0, "most": 0}

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def post(self, url, **kwargs):
                active["now"] += 1
                active["most"] = max(active["most"], active["now"])
                await asyncio.sleep(0.05)
                active["now"] -= 1
                return "response"

        monkeypatch.setattr(server.httpx, "AsyncClient", FakeClient)
        monkeypatch.setattr(server, "_locks", {})

        async def scenario():
            return await asyncio.gather(
                server.post(Modality.SPEECH, "/audio/speech", json={}),
                server.post(Modality.SPEECH, "/audio/speech", json={}),
            )

        assert asyncio.run(scenario()) == ["response", "response"]
        assert active["most"] == 1


class TestTranscriptionUploads:
    def test_one_bad_file_queues_none_of_them(self, monkeypatch):
        from fastapi.testclient import TestClient
        from backend.app.audio import jobs, routes
        from backend.app.main import app

        monkeypatch.setattr(routes, "server_base", lambda modality: "http://audio")
        submitted = []
        monkeypatch.setattr(
            jobs.transcription_queue, "submit", lambda **kwargs: submitted.append(kwargs)
        )

        response = TestClient(app).post(
            "/api/audio/transcriptions",
            files=[
                ("files", ("one.wav", b"RIFF....", "audio/wav")),
                ("files", ("empty.wav", b"", "audio/wav")),
            ],
        )

        assert response.status_code == 400
        assert submitted == []


class TestAudioImageRefresh:
    class Image:
        def __init__(self, version):
            self.labels = {"org.opencontainers.image.version": version} if version else {}

    @pytest.fixture
    def manager(self):
        from backend.app.inference.audio_runtimes import SpeechRuntime
        from backend.app.inference.manager import ServerManager, StatusBroadcaster

        instance = ServerManager.__new__(ServerManager)
        instance._runtime = SpeechRuntime()
        instance._broadcaster = StatusBroadcaster()
        instance._init_state()
        return instance

    def test_an_image_from_another_release_is_refreshed(self, manager, monkeypatch):
        monkeypatch.setattr(settings, "VERSION", "1.3.0")
        assert manager._is_from_another_release(self.Image("1.2.0")) is True
        assert manager._is_from_another_release(self.Image("1.3.0")) is False

    def test_a_development_build_never_refreshes(self, manager, monkeypatch):
        monkeypatch.setattr(settings, "VERSION", "dev")
        assert manager._is_from_another_release(self.Image("1.2.0")) is False

    def test_an_unlabelled_image_is_left_alone(self, manager, monkeypatch):
        monkeypatch.setattr(settings, "VERSION", "1.3.0")
        assert manager._is_from_another_release(self.Image(None)) is False

    def test_third_party_images_are_never_refreshed(self, monkeypatch):
        from backend.app.inference.manager import ServerManager, StatusBroadcaster
        from backend.app.inference.vllm_runtime import VLLMRuntime

        manager = ServerManager.__new__(ServerManager)
        manager._runtime = VLLMRuntime()
        manager._broadcaster = StatusBroadcaster()
        manager._init_state()
        monkeypatch.setattr(settings, "VERSION", "1.3.0")

        assert manager._is_from_another_release(self.Image("0.9.0")) is False
