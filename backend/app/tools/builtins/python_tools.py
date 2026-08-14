from typing import Optional
from backend.app.agent.history import truncate_tool_result
from backend.app.agent.sandbox import sandbox_manager
from backend.app.models import ToolCategory, ToolRisk
from backend.app.tools.metadata import tool_metadata

# Bounded like run_command: a stuck computation must not wedge the whole turn.
MAX_PYTHON_TIMEOUT_SECONDS = 600
DEFAULT_PYTHON_TIMEOUT_SECONDS = 60


@tool_metadata(category=ToolCategory.EXECUTION, risk=ToolRisk.HIGH)
async def run_python(
    code: str,
    timeout: Optional[int] = 60,
    container_id: Optional[str] = None,
) -> str:
    """Runs Python code in a persistent session: variables, imports, and loaded data are kept between calls.

    Use this instead of `python3 -c` one-liners for exploration and iterative work, so
    expensive setup (loading files, imports) is paid once. A bare expression on the last
    line echoes its value like a notebook cell. Install missing packages first with
    `run_command("pip install ...")`.

    Args:
        code: Python source to execute in the shared session.
        timeout: Maximum seconds to wait (default 60, max 600). A timeout restarts the session and loses its variables.
    """
    if not container_id:
        return (
            "Error: No container is available for this conversation, so the Python"
            " session cannot start."
        )

    if not timeout or timeout <= 0:
        timeout = DEFAULT_PYTHON_TIMEOUT_SECONDS
    timeout = min(int(timeout), MAX_PYTHON_TIMEOUT_SECONDS)

    result = await sandbox_manager.run_python_code(
        container_id=container_id, code=code, timeout=timeout
    )
    return truncate_tool_result(result)
