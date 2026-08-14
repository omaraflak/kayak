"""Tests for what a sandbox container is allowed to see.

The isolation promise is per-conversation. The whole data directory was once
mounted read-only at /data, which let any agent read every other conversation's
workspace, the conversation database, and settings.json -- the file holding
provider API keys in plaintext.
"""

from pathlib import Path

from backend.app.agent.sandbox import _build_volume_mounts, has_forbidden_data_mount


class FakeContainer:
    def __init__(self, mounts):
        self.attrs = {"Mounts": mounts}


class TestVolumeMounts:
    def test_only_the_conversation_workspace_is_mounted(self, tmp_path: Path):
        mounts = _build_volume_mounts(tmp_path / "workspaces" / "conv-1")

        assert len(mounts) == 1
        (config,) = mounts.values()
        assert config == {"bind": "/workspace", "mode": "rw"}

    def test_nothing_binds_the_data_directory(self, tmp_path: Path):
        mounts = _build_volume_mounts(tmp_path / "workspaces" / "conv-1")
        assert all(config["bind"] != "/data" for config in mounts.values())


class TestLegacyMountDetection:
    def test_a_container_with_the_data_mount_is_flagged(self):
        container = FakeContainer(
            [
                {"Source": "/host/ws/conv-1", "Destination": "/workspace"},
                {"Source": "/host/data", "Destination": "/data"},
            ]
        )
        assert has_forbidden_data_mount(container) is True

    def test_a_clean_container_is_not_flagged(self):
        container = FakeContainer(
            [{"Source": "/host/ws/conv-1", "Destination": "/workspace"}]
        )
        assert has_forbidden_data_mount(container) is False

    def test_a_container_with_no_mount_metadata_is_not_flagged(self):
        assert has_forbidden_data_mount(FakeContainer([])) is False
