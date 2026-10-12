from typing import Optional
from backend.app.agent.task_manager import task_manager
from backend.app.database import get_task
from backend.app.models import BackgroundTask
from backend.app.models import ToolCategory, ToolRisk
from backend.app.tools.metadata import tool_metadata


def _format_task_status(task: BackgroundTask) -> str:
    """Formats a BackgroundTask model into a descriptive status string with output tails."""
    output = [
        f"Task: {task.name} ({task.id})",
        f"Type: {task.task_type.value if hasattr(task.task_type, 'value') else task.task_type}",
        f"Status: {task.status.value if hasattr(task.status, 'value') else task.status}",
    ]
    if task.exit_code is not None:
        output.append(f"Exit Code: {task.exit_code}")

    if task.stdout:
        tail_stdout = task.stdout[-2000:] if len(task.stdout) > 2000 else task.stdout
        output.append(f"\n--- STDOUT (recent) ---\n{tail_stdout}")
    if task.stderr:
        tail_stderr = task.stderr[-2000:] if len(task.stderr) > 2000 else task.stderr
        output.append(f"\n--- STDERR (recent) ---\n{tail_stderr}")

    return "\n".join(output)


@tool_metadata(category=ToolCategory.EXECUTION, risk=ToolRisk.HIGH)
async def start_background_task(
    command: str,
    name: str,
    conversation_id: Optional[str] = None,
    container_id: Optional[str] = None,
) -> str:
    """Starts a long-running shell command or process in the background.

    Args:
        command: The shell command to run in the background.
        name: A descriptive label for the task (e.g. 'dev-server', 'build-job').
    """
    if not conversation_id or not container_id:
        return "Error: No container is available for this conversation."

    task = await task_manager.start_shell_task(
        conversation_id=conversation_id,
        name=name,
        command=command,
        container_id=container_id,
    )
    return (
        f"Background task '{name}' started successfully.\nTask ID: {task.id}\nUse"
        " `get_task_status(task_id)` to monitor its progress."
    )


@tool_metadata(category=ToolCategory.EXECUTION, risk=ToolRisk.LOW, read_only=True)
async def get_task_status(task_id: str) -> str:
    """Checks the status and recent stdout/stderr output of a background task.

    Args:
        task_id: The unique ID of the background task.
    """
    task = await get_task(task_id)
    if not task:
        return f"Error: Task with ID '{task_id}' not found."

    return _format_task_status(task)


@tool_metadata(category=ToolCategory.EXECUTION, risk=ToolRisk.MODERATE)
async def stop_task(task_id: str) -> str:
    """Terminates or cancels a running background task.

    Args:
        task_id: The ID of the task to terminate.
    """
    success = await task_manager.stop_task(task_id)
    if success:
        return f"Task '{task_id}' was stopped."
    return f"Failed to stop task '{task_id}'."
