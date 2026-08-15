"""Tests for the persistent Python session protocol.

The driver script is executed for real in a subprocess: it is exactly the code
shipped into the container, so passing here means the in-container behaviour,
not a simulation of it.
"""

import json
import subprocess
import sys


from backend.app.agent.python_repl import (
    PYTHON_REPL_DRIVER,
    RESPONSE_SENTINEL,
    demux_docker_frames,
    format_execution_result,
    scan_for_response,
)


def run_driver(codes: list[str]) -> list[dict]:
    """Feeds code cells to a real driver process and returns its responses."""
    requests = "".join(json.dumps({"code": code}) + "\n" for code in codes)
    completed = subprocess.run(
        [sys.executable, "-u", "-c", PYTHON_REPL_DRIVER],
        input=requests,
        capture_output=True,
        text=True,
        timeout=30,
    )
    responses = []
    # split("\n") rather than splitlines(): the parser splits on newlines only,
    # and the test must read the stream the same way.
    for line in completed.stdout.split("\n"):
        if line.startswith(RESPONSE_SENTINEL):
            responses.append(json.loads(line[len(RESPONSE_SENTINEL):]))
    return responses


class TestDriver:
    def test_state_persists_between_cells(self):
        # The whole reason this exists: expensive setup paid once, reused later.
        first, second = run_driver(["x = 41", "print(x + 1)"])
        assert first["failed"] is False
        assert second["output"] == "42\n"

    def test_a_bare_expression_echoes_its_value(self):
        (response,) = run_driver(["1 + 1"])
        assert response["output"] == "2\n"

    def test_stdout_and_stderr_are_both_captured(self):
        (response,) = run_driver(
            ["import sys\nprint('out')\nprint('err', file=sys.stderr)"]
        )
        assert "out" in response["output"]
        assert "err" in response["output"]

    def test_an_exception_reports_failure_without_killing_the_session(self):
        first, second = run_driver(["1 / 0", "print('still alive')"])
        assert first["failed"] is True
        assert "ZeroDivisionError" in first["output"]
        assert second["output"] == "still alive\n"

    def test_sys_exit_cannot_end_the_session(self):
        first, second = run_driver(["import sys; sys.exit(1)", "print('survived')"])
        assert first["failed"] is True
        assert second["output"] == "survived\n"


class TestDemuxDockerFrames:
    def frame(self, payload: bytes) -> bytes:
        return b"\x01\x00\x00\x00" + len(payload).to_bytes(4, "big") + payload

    def test_a_complete_frame_yields_its_payload(self):
        payload, rest = demux_docker_frames(self.frame(b"hello"))
        assert payload == b"hello"
        assert rest == b""

    def test_a_split_frame_is_kept_until_complete(self):
        data = self.frame(b"hello")
        payload, rest = demux_docker_frames(data[:7])
        assert payload == b""
        assert rest == data[:7]

        payload, rest = demux_docker_frames(rest + data[7:])
        assert payload == b"hello"
        assert rest == b""

    def test_multiple_frames_concatenate(self):
        payload, rest = demux_docker_frames(self.frame(b"a") + self.frame(b"b"))
        assert payload == b"ab"
        assert rest == b""


class TestScanForResponse:
    def test_finds_the_response_line(self):
        buffer = RESPONSE_SENTINEL + '{"output": "hi\\n", "failed": false}\n'
        response, leaked, rest = scan_for_response(buffer)
        assert response == {"output": "hi\n", "failed": False}
        assert leaked == ""
        assert rest == ""

    def test_output_bypassing_capture_is_preserved_not_dropped(self):
        # A subprocess spawned by user code writes straight to the terminal.
        buffer = (
            "raw subprocess output\n"
            + RESPONSE_SENTINEL
            + '{"output": "", "failed": false}\n'
        )
        response, leaked, rest = scan_for_response(buffer)
        assert response is not None
        assert leaked == "raw subprocess output\n"

    def test_an_incomplete_line_waits_for_more_data(self):
        response, leaked, rest = scan_for_response("partial output with no newline")
        assert response is None
        assert leaked == ""
        assert rest == "partial output with no newline"


class TestFormatExecutionResult:
    def test_success_returns_the_combined_output(self):
        result = format_execution_result("leak\n", {"output": "value\n", "failed": False})
        assert result == "leak\nvalue"

    def test_silence_is_stated_explicitly(self):
        assert format_execution_result("", {"output": "", "failed": False}) == (
            "Executed with no output."
        )

    def test_failure_carries_the_error_prefix_the_engine_looks_for(self):
        result = format_execution_result("", {"output": "Traceback...", "failed": True})
        assert result.startswith("Error:")
        assert "Traceback" in result
