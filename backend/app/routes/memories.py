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


class MemoryEdit(BaseModel):
    """Changes one memory; an empty replacement forgets it."""
    previous: str
    replacement: str


@router.put("", response_model=MemoryList)
async def replace_memories(request: MemoryList) -> MemoryList:
    """Overwrites the whole list."""
    return MemoryList(memories=memory_store.replace(request.memories))


@router.patch("", response_model=MemoryList)
async def edit_memory(request: MemoryEdit) -> MemoryList:
    """Rewrites or forgets one memory, leaving every other one as it is now.

    Raises:
        HTTPException: 409 if that memory changed since the page loaded it.
    """
    try:
        return MemoryList(memories=memory_store.edit(request.previous, request.replacement))
    except LookupError:
        raise HTTPException(
            status_code=409,
            detail="This memory changed since the page loaded. It has been refreshed; try again.",
        )
