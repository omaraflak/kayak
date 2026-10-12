"""Tests for choosing where to reach a local model server.

The default was `host.docker.internal`, which only resolves from inside a container.
Running Kayak directly on the Mac, as the development setup does, meant a model
could be downloaded, started and reported healthy, while every conversation using it
failed with a connection error.
"""

from pathlib import Path

from backend.app.config import local_api_base, running_in_container, settings


class TestLocalApiBase:
    def test_a_containerised_kayak_reaches_out_through_the_docker_host(self, monkeypatch):
        monkeypatch.setattr(settings, "RUNNING_IN_CONTAINER", True)

        assert local_api_base(8001) == "http://host.docker.internal:8001/v1"

    def test_a_kayak_on_the_mac_uses_the_published_port_directly(self, monkeypatch):
        # host.docker.internal does not resolve here, which is what broke chatting
        # with a model that was otherwise serving perfectly well.
        monkeypatch.setattr(settings, "RUNNING_IN_CONTAINER", False)

        assert local_api_base(8001) == "http://localhost:8001/v1"
        assert local_api_base(9100) == "http://localhost:9100/v1"


class TestRunningInContainer:
    def test_the_docker_marker_file_is_enough(self, tmp_path: Path):
        marker = tmp_path / ".dockerenv"
        marker.touch()

        assert running_in_container(dockerenv=marker) is True

    def test_the_mac_itself_is_not_a_container(self, tmp_path: Path):
        assert running_in_container(dockerenv=tmp_path / "absent") is False
