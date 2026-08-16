"""Tests for choosing where to reach the local vLLM server.

The default was `host.docker.internal`, which only resolves from inside a container.
Running Kayak directly on the host, as the development setup does, meant a model
could be downloaded, started and reported healthy, while every conversation using it
failed with a connection error.
"""

from pathlib import Path

from backend.app.config import default_vllm_api_base, running_in_container


class TestDefaultVllmApiBase:
    def test_a_containerised_kayak_reaches_out_through_the_docker_host(self):
        assert default_vllm_api_base(8001, in_container=True) == (
            "http://host.docker.internal:8001/v1"
        )

    def test_a_host_kayak_uses_the_published_port_directly(self):
        # host.docker.internal does not resolve here, which is what broke chatting
        # with a model that was otherwise serving perfectly well.
        assert default_vllm_api_base(8001, in_container=False) == "http://localhost:8001/v1"

    def test_the_configured_port_is_honoured(self):
        assert default_vllm_api_base(9100, in_container=False) == "http://localhost:9100/v1"
        assert default_vllm_api_base(9100, in_container=True) == (
            "http://host.docker.internal:9100/v1"
        )


class TestRunningInContainer:
    def test_the_docker_marker_file_is_enough(self, tmp_path: Path):
        marker = tmp_path / ".dockerenv"
        marker.touch()

        assert running_in_container(dockerenv=marker, cgroup=tmp_path / "absent") is True

    def test_a_container_runtime_in_the_cgroups_counts(self, tmp_path: Path):
        cgroup = tmp_path / "cgroup"
        cgroup.write_text("0::/docker/3d7a9f2b1c\n")

        assert running_in_container(dockerenv=tmp_path / "absent", cgroup=cgroup) is True

    def test_kubernetes_counts_too(self, tmp_path: Path):
        cgroup = tmp_path / "cgroup"
        cgroup.write_text("0::/kubepods/besteffort/pod123\n")

        assert running_in_container(dockerenv=tmp_path / "absent", cgroup=cgroup) is True

    def test_an_ordinary_host_is_not_a_container(self, tmp_path: Path):
        cgroup = tmp_path / "cgroup"
        cgroup.write_text("0::/user.slice/user-501.slice\n")

        assert running_in_container(dockerenv=tmp_path / "absent", cgroup=cgroup) is False

    def test_a_machine_without_cgroups_is_not_a_container(self, tmp_path: Path):
        # macOS has neither file; the probe must answer rather than raise.
        assert running_in_container(
            dockerenv=tmp_path / "absent", cgroup=tmp_path / "also-absent"
        ) is False
