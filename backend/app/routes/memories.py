"""What Kayak has learned from the user.

Deliberately not nested under an agent: memories describe the user, and every agent
reads the same list.
"""

from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.app.memories.store import memory_store

router = APIRouter(prefix="/api/memories", tags=["memories"])


class MemoryList(BaseModel):
    """Everything Kayak has been taught, oldest first."""
    memories: List[str]


class NewMemory(BaseModel):
    """One thing to teach Kayak."""
    content: str


@router.get("", response_model=MemoryList)
async def list_memories() -> MemoryList:
    """Everything Kayak has learned from the user."""
    return MemoryList(memories=memory_store.list_memories())


@router.post("", response_model=MemoryList)
async def teach(request: NewMemory) -> MemoryList:
    """Adds one memory, from the user rather than from an agent.

    Raises:
        HTTPException: 400 if the memory is empty.
    """
    try:
        return MemoryList(memories=memory_store.add(request.content))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


@router.put("", response_model=MemoryList)
async def replace_memories(request: MemoryList) -> MemoryList:
    """Overwrites the whole list, for editing and forgetting."""
    return MemoryList(memories=memory_store.replace(request.memories))
