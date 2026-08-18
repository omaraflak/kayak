"""Tests for the file channel Kayak uses to ask the launcher for Metal.

The two sides run in different places -- Kayak in a container, the launcher on
the host -- and neither can assume the other is present or mid-write, so the
cases that matter are the degraded ones.
"""

import json

import pytest

from backend.app.vllm import metal


@pytest.fixture
def control(tmp_path, monkeypatch):
    """Points the control channel at a temporary data directory."""
    monkeypatch.setattr(metal.settings, "DATA_DIR", tmp_path)
    return tmp_path / metal.CONTROL_DIRNAME


def write_status(control_dir, payload):
    control_dir.mkdir(parents=True, exist_ok=True)
    (control_dir / metal.STATUS_FILENAME).write_text(json.dumps(payload), encoding="utf-8")


def test_no_launcher_reports_unsupported(control):
    """Kayak run from docker-compose has no launcher writing status at all."""
    status = metal.read_status()

    assert status.supported is False
    assert status.state == "stopped"


def test_half_written_status_reports_unsupported(control):
    """The launcher rewrites this file every few seconds; a torn read is transient."""
    control.mkdir(parents=True, exist_ok=True)
    (control / metal.STATUS_FILENAME).write_text('{"metal": {"suppo', encoding="utf-8")

    assert metal.read_status().supported is False


def test_status_is_read_from_the_launcher(control):
    write_status(
        control,
        {
            "metal": {
                "supported": True,
                "installed": True,
                "state": "ready",
                "model": "mlx-community/Qwen3.8-27B-8bit",
                "port": 8001,
                "error": None,
            }
        },
    )

    status = metal.read_status()

    assert status.supported is True
    assert status.state == "ready"
    assert status.model == "mlx-community/Qwen3.8-27B-8bit"
    assert status.port == 8001


def test_unknown_fields_do_not_break_an_older_kayak(control):
    """A newer launcher may report keys this build has never heard of."""
    write_status(
        control,
        {"metal": {"supported": True, "state": "ready", "future_field": 1}, "other": 2},
    )

    assert metal.read_status().supported is True


def test_desired_state_is_written_for_the_launcher(control):
    metal.write_desired(running=True, model="mlx-community/X")

    payload = json.loads((control / metal.DESIRED_FILENAME).read_text(encoding="utf-8"))

    assert payload["metal"]["running"] is True
    assert payload["metal"]["model"] == "mlx-community/X"


def test_writing_desired_state_leaves_no_partial_file(control):
    """The launcher polls this path, so a rename is the only safe replacement."""
    metal.write_desired(running=True, model="mlx-community/X")
    metal.write_desired(running=False)

    names = [path.name for path in control.iterdir()]

    assert names == [metal.DESIRED_FILENAME]
    payload = json.loads((control / metal.DESIRED_FILENAME).read_text(encoding="utf-8"))
    assert payload["metal"]["running"] is False


@pytest.mark.parametrize(
    "model_id",
    [
        "mlx-community/Qwen3.8-27B-8bit",
        "MLX-Community/Llama-3.2-3B-Instruct-4bit",
    ],
)
def test_accepts_mlx_repositories(model_id):
    assert metal.is_mlx_model(model_id) is True


@pytest.mark.parametrize(
    "model_id",
    [
        "Qwen/Qwen2.5-Coder-7B-Instruct",
        "mlx-community",
        "mlx-community/",
        "mlx-community/a/b",
        "",
    ],
)
def test_rejects_anything_metal_cannot_serve(model_id):
    """These fail minutes into a download, so they are refused before asking."""
    assert metal.is_mlx_model(model_id) is False
