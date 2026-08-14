"""Tests for working-directory persistence across run_command calls.

Each call is still a fresh shell -- a hung command cannot wedge a session --
but the ending directory carries into the next call. The wrapper runs through
a real shell here, so what passes is the exact behaviour in the container.
"""

import subprocess
from pathlib import Path

from backend.app.tools.builtins.command_tools import (
    CWD_SENTINEL,
    extract_cwd,
    run_command,
    wrap_with_cwd_tracking,
)


def run_in_shell(command: str, cwd: str, fallback: str) -> subprocess.CompletedProcess:
    wrapped = wrap_with_cwd_tracking(command, cwd, fallback)
    return subprocess.run(
        ["/bin/bash", "-c", wrapped], capture_output=True, text=True, timeout=15
    )


class TestWrapper:
    def test_reports_where_cd_ended_up(self, tmp_path: Path):
        (tmp_path / "sub").mkdir()
        result = run_in_shell("cd sub && echo inside", str(tmp_path), str(tmp_path))
        output, cwd = extract_cwd(result.stdout, str(tmp_path))
        assert "inside" in output
        assert cwd == str(tmp_path / "sub")

    def test_preserves_the_failing_exit_code(self, tmp_path: Path):
        # The trailing directory report must not make failures look successful.
        result = run_in_shell("false", str(tmp_path), str(tmp_path))
        assert result.returncode == 1
        assert CWD_SENTINEL in result.stdout

    def test_a_missing_start_directory_falls_back(self, tmp_path: Path):
        result = run_in_shell("pwd", str(tmp_path / "deleted"), str(tmp_path))
        _, cwd = extract_cwd(result.stdout, str(tmp_path / "deleted"))
        assert cwd == str(tmp_path)

    def test_an_explicit_exit_skips_the_report_and_keeps_its_code(self, tmp_path: Path):
        result = run_in_shell("exit 7", str(tmp_path), str(tmp_path))
        assert result.returncode == 7
        _, cwd = extract_cwd(result.stdout, "/previous")
        assert cwd == "/previous"


class TestExtractCwd:
    def test_strips_the_sentinel_from_the_output(self):
        output, cwd = extract_cwd(f"hello\n{CWD_SENTINEL}/workspace/sub", "/workspace")
        assert output == "hello"
        assert cwd == "/workspace/sub"

    def test_stderr_after_the_sentinel_survives(self):
        raw = f"ok\n{CWD_SENTINEL}/workspace\nSTDERR:\nwarning"
        output, cwd = extract_cwd(raw, "/workspace")
        assert "warning" in output
        assert CWD_SENTINEL not in output

    def test_no_sentinel_keeps_the_previous_directory(self):
        raw = "Error: Command timed out after 60 seconds and was killed."
        output, cwd = extract_cwd(raw, "/workspace/deep")
        assert output == raw
        assert cwd == "/workspace/deep"


class TestRunCommandPersistence:
    async def test_cd_carries_into_the_next_call(self, tmp_path: Path):
        (tmp_path / "project").mkdir()
        first = await run_command(
            "cd project && touch marker.txt",
            workspace_dir=tmp_path,
            conversation_id="cwd-test-1",
        )
        assert not first.startswith("Error")

        second = await run_command(
            "pwd && ls", workspace_dir=tmp_path, conversation_id="cwd-test-1"
        )
        assert str(tmp_path / "project") in second
        assert "marker.txt" in second

    async def test_conversations_do_not_share_a_directory(self, tmp_path: Path):
        (tmp_path / "a").mkdir()
        await run_command("cd a", workspace_dir=tmp_path, conversation_id="cwd-test-2")
        other = await run_command(
            "pwd", workspace_dir=tmp_path, conversation_id="cwd-test-3"
        )
        assert str(tmp_path) in other
        assert str(tmp_path / "a") not in other

    async def test_a_failing_command_still_reports_its_exit_code(self, tmp_path: Path):
        result = await run_command(
            "ls /definitely-not-here",
            workspace_dir=tmp_path,
            conversation_id="cwd-test-4",
        )
        assert "[Exit code:" in result
