import asyncio
from pathlib import Path
from typing import Optional
from backend.app.agent.history import truncate_tool_result
from backend.app.agent.sandbox import sandbox_manager
from backend.app.models import ToolCategory, ToolRisk
from backend.app.tools.metadata import tool_metadata

# Upper bound on how long a foreground command may block an agent turn. Anything
# longer belongs in start_background_task, which streams instead of blocking.
MAX_COMMAND_TIMEOUT_SECONDS = 600
DEFAULT_COMMAND_TIMEOUT_SECONDS = 60


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
    return "\n".join(output) if output else "Command executed with no output."


@tool_metadata(category=ToolCategory.EXECUTION, risk=ToolRisk.HIGH)
async def run_command(
    command: str,
    timeout: Optional[int] = 60,
    workspace_dir: Optional[Path] = None,
    container_id: Optional[str] = None,
) -> str:
    """Executes a shell command in the workspace environment or isolated container and returns stdout and stderr.

    Args:
        command: The shell command line string to execute.
        timeout: Maximum execution time in seconds before terminating (default 60s, max 600s).
    """
    timeout = _clamp_timeout(timeout)

    if container_id:
        try:
            return await sandbox_manager.exec_command(
                container_id=container_id, command=command, timeout=timeout
            )
        except Exception as e:
            return (
                f"Error executing command in sandbox container"
                f" '{container_id}': {str(e)}"
            )

    cwd = workspace_dir if workspace_dir else Path.cwd()
    try:
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd),
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return f"Error: Command timed out after {timeout} seconds."

        stdout_str = stdout.decode("utf-8", errors="replace")
        stderr_str = stderr.decode("utf-8", errors="replace")

        return _format_process_output(stdout_str, stderr_str, process.returncode)

    except Exception as e:
        return f"Error executing command: {str(e)}"
