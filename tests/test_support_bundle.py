"""Tests for the diagnostics bundle.

The bundle exists because a user reporting a problem has access to neither
side's output: the server logs to a stdout owned by Docker, and the launcher
runs on the host. Both have to end up in one document.
"""

import logging

import pytest

from backend.app import support
from backend.app.vllm import metal


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(support.settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(metal.settings, "DATA_DIR", tmp_path)
    return tmp_path


def test_recent_logs_are_captured():
    handler = support.install()
    logging.getLogger("kayak.test").error("something went wrong")

    assert any("something went wrong" in line for line in support.recent_logs())
    assert handler is support.install(), "the handler must be installed once"


def test_bundle_reports_both_versions(data_dir):
    control = data_dir / metal.CONTROL_DIRNAME
    control.mkdir(parents=True)
    (control / metal.STATUS_FILENAME).write_text(
        '{"metal": {}, "versions": {"launcher": "9.9.9", "kayak": "8.8.8"}}',
        encoding="utf-8",
    )

    bundle = support.build_bundle()

    assert "9.9.9" in bundle
    assert "Kayak version:" in bundle


def test_bundle_survives_a_missing_launcher(data_dir):
    """Kayak run from docker-compose has no launcher and no launcher log."""
    bundle = support.build_bundle()

    assert "Launcher version: not running" in bundle
    assert "===== Launcher log =====" in bundle


def test_bundle_includes_the_launcher_log(data_dir):
    control = data_dir / metal.CONTROL_DIRNAME
    control.mkdir(parents=True)
    (control / support.LAUNCHER_LOG).write_text(
        "[metal] install failed: uv not found\n", encoding="utf-8"
    )

    assert "uv not found" in support.build_bundle()


def test_launcher_log_is_truncated_to_a_tail(data_dir):
    """A long-running launcher's log must not make the bundle unsendable."""
    control = data_dir / metal.CONTROL_DIRNAME
    control.mkdir(parents=True)
    body = "\n".join(f"line {index}" for index in range(support.LAUNCHER_LOG_LINES * 3))
    (control / support.LAUNCHER_LOG).write_text(body, encoding="utf-8")

    bundle = support.build_bundle()

    assert "line 0" not in bundle
    assert f"line {support.LAUNCHER_LOG_LINES * 3 - 1}" in bundle


def test_undecodable_launcher_log_does_not_break_the_bundle(data_dir):
    """A truncated write can leave invalid UTF-8 partway through a line."""
    control = data_dir / metal.CONTROL_DIRNAME
    control.mkdir(parents=True)
    (control / support.LAUNCHER_LOG).write_bytes(b"good line\n\xff\xfe broken\n")

    assert "good line" in support.build_bundle()
