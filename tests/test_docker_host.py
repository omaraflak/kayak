"""Tests for translating container paths into Mac paths Docker can mount.

Sandboxes are siblings on Docker Desktop, so the workspace mounted into one has to be
named by its path on the Mac, not by `/app/data/...` inside Kayak's own container.
"""

from pathlib import Path

import pytest

from backend.app import docker_host


@pytest.fixture
def mounts(monkeypatch):
    def install(mapping):
        monkeypatch.setattr(docker_host, "_mounts", mapping)

    return install


def test_a_path_under_a_mount_is_translated(mounts, tmp_path: Path):
    mounts({str(tmp_path.resolve()): "/Users/me/Library/Application Support/Kayak/data"})

    assert docker_host.host_path(tmp_path / "workspaces" / "c1") == (
        "/Users/me/Library/Application Support/Kayak/data/workspaces/c1"
    )


def test_the_mount_point_itself_is_translated(mounts, tmp_path: Path):
    mounts({str(tmp_path.resolve()): "/Users/me/kayak/data"})

    assert docker_host.host_path(tmp_path) == "/Users/me/kayak/data"


def test_a_sibling_sharing_a_prefix_is_not_translated(mounts, tmp_path: Path):
    mounts({str((tmp_path / "data").resolve()): "/Users/me/data"})

    other = tmp_path / "data-other" / "file"
    assert docker_host.host_path(other) == str(other.resolve())


def test_without_mounts_paths_are_already_mac_paths(mounts, tmp_path: Path):
    # Kayak running directly on the Mac.
    mounts({})

    assert docker_host.host_path(tmp_path / "x") == str((tmp_path / "x").resolve())


def test_the_declared_data_directory_wins(monkeypatch):
    monkeypatch.setenv("KAYAK_HOST_DATA_DIR", "/Users/me/kayak/data/")

    assert docker_host._discover_mounts() == {
        str(docker_host.settings.DATA_DIR): "/Users/me/kayak/data"
    }
