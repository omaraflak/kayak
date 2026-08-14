import asyncio
from pathlib import Path
import re
import shlex
from typing import Dict, Optional, Tuple
from backend.app.agent.history import truncate_tool_result
from backend.app.agent.sandbox import sandbox_manager
from backend.app.models import ToolCategory, ToolRisk
from backend.app.tools.metadata import tool_metadata

# Upper bound on how long a foreground command may block an agent turn. Anything
# longer belongs in start_background_task, which streams instead of blocking.
MAX_COMMAND_TIMEOUT_SECONDS = 600
DEFAULT_COMMAND_TIMEOUT_SECONDS = 60

#: Marks where the shell reports its final working directory. Each call is still
#: a fresh shell -- deliberately, so a hung command cannot wedge a session -- but
#: the ending directory is carried into the next call, the way Claude Code's
#: shell tool behaves: `cd project && npm test` leaves the agent in `project`.
CWD_SENTINEL = "__KAYAK_CWD__"
_CWD_PATTERN = re.compile(rf"\n?{CWD_SENTINEL}([^\n]*)")

#: Last known working directory per conversation. In-memory on purpose: after a
#: restart the fallback is simply the workspace root, which is also where every
#: conversation starts.
_conversation_cwds: Dict[str, str] = {}


def wrap_with_cwd_tracking(command: str, cwd: str, fallback_dir: str) -> str:
    """Wraps a command so the shell reports where it ended up.

    The command's own exit status is captured and re-raised after the report:
    without that, the trailing printf would make every failing command look
    successful.
    """
    return (
        f"cd {shlex.quote(cwd)} 2>/dev/null || cd {shlex.quote(fallback_dir)}\n"
        f"{command}\n"
        "__kayak_status=$?\n"
        f"printf '\\n{CWD_SENTINEL}%s' \"$PWD\"\n"
        "exit $__kayak_status"
    )


def extract_cwd(output: str, fallback_cwd: str) -> Tuple[str, str]:
    """Splits a command's output from the working-directory report.

    The sentinel is searched for rather than assumed last: stderr is appended
    after stdout in the combined output. A missing sentinel (timeout, `exit` in
    the command) keeps the previous directory.

    Returns:
        Tuple[str, str]: (clean output, working directory for the next call).
    """
    match = _CWD_PATTERN.search(output)
    if not match:
        return output, fallback_cwd
    cleaned = (output[: match.start()] + output[match.end():]).rstrip("\n")
    reported = match.group(1).strip()
    return cleaned, reported or fallback_cwd


def current_cwd(conversation_id: Optional[str], default_dir: str) -> str:
    """The directory the conversation's next command starts in."""
    if not conversation_id:
        return default_dir
    return _conversation_cwds.get(conversation_id, default_dir)


def remember_cwd(conversation_id: Optional[str], cwd: str) -> None:
    """Records where the conversation's shell ended up."""
    if conversation_id:
        _conversation_cwds[conversation_id] = cwd


def _clamp_timeout(timeout: Optional[int]) -> int:
    """Clamps a model-supplied timeout into a range that cannot wedge a turn."""
    if not timeout or timeout <= 0:
        return DEFAULT_COMMAND_TIMEOUT_SECONDS
    return min(int(timeout), MAX_COMMAND_TIMEOUT_SECONDS)


def _format_process_output(stdout_str: str, stderr_str: str, exit_code: int) -> str:
    """Formats standard output, standard error, and exit code into a response string."""
    output = []
    if stdout_str:
        output.append(truncate_tool_result(stdout_str))
    if stderr_str:
        output.append(f"STDERR:\n{truncate_tool_result(stderr_str)}")
    if exit_code != 0:
        output.append(f"\n[Exit code: {exit_code}]")
    return "\n".join(output) if output else ""


@tool_metadata(category=ToolCategory.EXECUTION, risk=ToolRisk.HIGH)
async def run_command(
    command: str,
    timeout: Optional[int] = 60,
    workspace_dir: Optional[Path] = None,
    container_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> str:
    """Executes a shell command and returns stdout and stderr. The working directory persists between calls; environment variables do not.

    Args:
        command: The shell command line string to execute.
        timeout: Maximum execution time in seconds before terminating (default 60s, max 600s).
    """
    timeout = _clamp_timeout(timeout)
    default_dir = "/workspace" if container_id else str(workspace_dir or Path.cwd())
    cwd = current_cwd(conversation_id, default_dir)
    wrapped = wrap_with_cwd_tracking(command, cwd, default_dir)

    if container_id:
        try:
            raw = await sandbox_manager.exec_command(
                container_id=container_id, command=wrapped, timeout=timeout
            )
        except Exception as e:
            return (
                f"Error executing command in sandbox container"
                f" '{container_id}': {str(e)}"
            )
    else:
        try:
            process = await asyncio.create_subprocess_shell(
                wrapped,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=default_dir,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return f"Error: Command timed out after {timeout} seconds."

            raw = _format_process_output(
                stdout.decode("utf-8", errors="replace"),
                stderr.decode("utf-8", errors="replace"),
                process.returncode,
            )
        except Exception as e:
            return f"Error executing command: {str(e)}"

    output, new_cwd = extract_cwd(raw, cwd)
    remember_cwd(conversation_id, new_cwd)
    return output if output.strip() else "Command executed with no output."
