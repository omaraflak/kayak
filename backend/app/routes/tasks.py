from typing import Dict, List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from backend.app.agent.task_manager import task_manager
from backend.app.database import get_task, list_tasks
from backend.app.models import BackgroundTask

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


class TaskInputRequest(BaseModel):
    """Request payload for sending stdin input to a running task."""
    input: str


@router.get("", response_model=List[BackgroundTask])
async def get_all_tasks(conversation_id: Optional[str] = None) -> List[BackgroundTask]:
    """Lists background tasks, optionally filtered by conversation identifier.

    Args:
        conversation_id: Optional filter for a specific conversation session.

    Returns:
        A list of matching BackgroundTask objects.
    """
    return await list_tasks(conversation_id=conversation_id)


@router.get("/{task_id}", response_model=BackgroundTask)
async def get_task_details(task_id: str) -> BackgroundTask:
    """Retrieves full details and live stdout/stderr for a specific background task.

    Args:
        task_id: Unique background task identifier.

    Returns:
        The BackgroundTask record with status and outputs.

    Raises:
        HTTPException: If the requested task is not found.
    """
    task = await get_task(task_id)
    if not task:
        raise HTTPException(
            status_code=404, detail=f"Task '{task_id}' not found"
        )
    return task


@router.post("/{task_id}/stop")
async def stop_running_task(task_id: str) -> Dict[str, str]:
    """Stops or terminates an active background subprocess or subagent.

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


@router.post("/{task_id}/input")
async def send_input_to_task(task_id: str, request: TaskInputRequest) -> Dict[str, str]:
    """Streams stdin text input to a running task process.

    Args:
        task_id: Unique background task identifier.
        request: TaskInputRequest containing stdin text payload.

    Returns:
        Status response dictionary indicating whether the input was sent.

    Raises:
        HTTPException: If the task is not found.
    """
    task = await get_task(task_id)
    if not task:
        raise HTTPException(
            status_code=404, detail=f"Task '{task_id}' not found"
        )

    success = await task_manager.send_input(task_id, request.input)
    return {"status": "sent" if success else "failed"}
