from typing import Dict, List, Optional
from fastapi import APIRouter, HTTPException
from backend.app.agent.task_manager import task_manager
from backend.app.database import get_task, list_tasks
from backend.app.models import BackgroundTask

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("", response_model=List[BackgroundTask])
async def get_all_tasks(conversation_id: Optional[str] = None) -> List[BackgroundTask]:
    """Lists background tasks, optionally filtered by conversation identifier.

    Filtering by conversation includes the tasks its sub-agents started: they run in
    the same container, and the conversation that delegated the work is where someone
    looks for a process it left running.

    Args:
        conversation_id: Optional filter for a specific conversation session.

    Returns:
        A list of matching BackgroundTask objects.
    """
    return await list_tasks(conversation_id=conversation_id, include_subagents=True)


@router.post("/{task_id}/stop")
async def stop_running_task(task_id: str) -> Dict[str, str]:
    """Stops a running background command or sub-agent.

    Args:
        task_id: Unique background task identifier.

    Returns:
        Status response dictionary indicating whether the task was stopped.

    Raises:
        HTTPException: If the task is not found.
    """
    task = await get_task(task_id)
    if not task:
        raise HTTPException(
            status_code=404, detail=f"Task '{task_id}' not found"
        )

    success = await task_manager.stop_task(task_id)
    return {"status": "stopped" if success else "failed"}
