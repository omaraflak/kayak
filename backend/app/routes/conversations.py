import asyncio
import json
import logging
from pathlib import Path
import shutil
from typing import Any, AsyncGenerator, Dict, List, Optional, Set
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from backend.app.agent.approvals import approval_registry
from backend.app.agent.engine import agent_engine
from backend.app.agent.sandbox import sandbox_manager
from backend.app.agent.task_manager import task_manager
from backend.app.agents.manager import agent_manager
from backend.app.config import settings
from backend.app.database import (
    add_message,
    create_conversation,
    delete_conversation,
    get_conversation,
    get_messages,
    list_child_conversations,
    list_conversations,
    update_conversation,
)
from backend.app.llm import generate_title
from backend.app.models import (
    Conversation,
    ConversationStatus,
    CreateConversationRequest,
    Message,
    MessageRole,
    SendMessageRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/conversations", tags=["conversations"])

# A slow or backgrounded browser tab must not grow its queue without bound. When the
# queue overflows the client is dropped; it reconnects and reloads from the database.
_EVENT_QUEUE_MAXSIZE = 1000

# Active event queues per conversation for SSE broadcasting
_conversation_event_queues: Dict[str, List[asyncio.Queue[Dict[str, Any]]]] = {}

# Active running agent tasks per conversation for cancellation
_running_agent_tasks: Dict[str, asyncio.Task[Any]] = {}

# In-progress turn buffers to replay full thoughts/tokens upon client reconnect
_active_turn_buffers: Dict[str, Dict[str, Any]] = {}

# Fire-and-forget tasks (title generation) held so they are not garbage collected.
_side_tasks: Set[asyncio.Task[Any]] = set()


class ToolApprovalRequest(BaseModel):
    """Payload recording the user's decision on a gated tool call."""
    approved: bool


def _spawn_side_task(coro) -> asyncio.Task[Any]:
    """Schedules a background coroutine and keeps a reference until it completes."""
    task = asyncio.create_task(coro)
    _side_tasks.add(task)
    task.add_done_callback(_side_tasks.discard)
    return task


def _broadcast_event(conversation_id: str, event: Dict[str, Any]) -> None:
    """Pushes a real-time event to all listening SSE client queues for a conversation."""
    for event_queue in list(_conversation_event_queues.get(conversation_id, [])):
        try:
            event_queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning(
                "Dropping SSE listener for conversation %s: client is not draining events.",
                conversation_id,
            )


# Public alias for external callers
broadcast_event = _broadcast_event


def _clean_initial_title(prompt: Optional[str]) -> Optional[str]:
    """Derives a provisional title from the first words of an initial message."""
    if not prompt:
        return None
    clean = " ".join(prompt.strip().split())
    return clean[:36].rsplit(" ", 1)[0] + "..." if len(clean) > 36 else clean


async def _async_generate_and_update_title(conversation_id: str, prompt: str, model_name: str) -> None:
    """Asynchronously generates an LLM title in the background without delaying conversation creation."""
    try:
        generated = await generate_title(prompt, model=model_name)
        if generated:
            await update_conversation(conversation_id, title=generated)
            _broadcast_event(conversation_id, {"type": "title_updated", "title": generated})
    except Exception:
        logger.debug("Title generation failed for %s", conversation_id, exc_info=True)


async def _run_agent_turn(conversation_id: str, agent_id: str) -> None:
    """Background task executing the agent turn loop and broadcasting events."""
    try:
        conversation = await get_conversation(conversation_id)
        if not conversation:
            return

        workspace_directory = settings.WORKSPACES_DIR / conversation_id
        _active_turn_buffers[conversation_id] = {"thinking": "", "tokens": ""}

        async for event in agent_engine.run(
            conversation_id=conversation_id,
            agent_id=agent_id,
            workspace_dir=workspace_directory,
            container_id=conversation.container_id,
        ):
            if conversation_id in _active_turn_buffers:
                if event.get("type") == "thinking":
                    _active_turn_buffers[conversation_id]["thinking"] += event.get("content", "")
                elif event.get("type") == "token":
                    _active_turn_buffers[conversation_id]["tokens"] += event.get("content", "")

            _broadcast_event(conversation_id, event)
    except asyncio.CancelledError:
        await update_conversation(conversation_id, status=ConversationStatus.ACTIVE)
        _broadcast_event(conversation_id, {"type": "cancelled"})
        raise
    except Exception as error:
        logger.exception("Agent turn failed for conversation %s", conversation_id)
        await update_conversation(conversation_id, status=ConversationStatus.ACTIVE)
        _broadcast_event(conversation_id, {"type": "error", "error": str(error)})
    finally:
        _active_turn_buffers.pop(conversation_id, None)
        _running_agent_tasks.pop(conversation_id, None)


async def _cancel_running_turn(conversation_id: str) -> bool:
    """Cancels an in-flight turn and waits for its cleanup to finish.

    Awaiting the cancelled task matters: the engine writes placeholder results for
    tool calls that never ran, and starting the next turn before that lands would
    read a history with dangling tool calls.
    """
    # Unblock the engine if it is parked waiting on a tool approval.
    approval_registry.cancel_conversation(conversation_id)

    task = _running_agent_tasks.get(conversation_id)
    if not task or task.done():
        return False

    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass
    return True


async def shutdown_active_turns() -> None:
    """Cancels every in-flight agent turn during server shutdown."""
    for conversation_id in list(_running_agent_tasks.keys()):
        await _cancel_running_turn(conversation_id)


@router.post("", response_model=Conversation)
async def create_new_conversation(request: CreateConversationRequest) -> Conversation:
    """Creates a new conversation record immediately and optionally initializes an isolated Docker sandbox."""
    title = request.title or _clean_initial_title(request.initial_message)

    conversation = await create_conversation(
        title=title or "New Conversation",
        agent_id=request.agent_id,
        isolated_container=request.isolated_container,
    )

    # Launch background LLM title generation without blocking UI response
    if not request.title and request.initial_message:
        agent_config = agent_manager.get_agent(request.agent_id)
        model_name = agent_config.model if agent_config else settings.DEFAULT_MODEL
        _spawn_side_task(
            _async_generate_and_update_title(
                conversation.id, request.initial_message, model_name
            )
        )

    workspace_directory = settings.WORKSPACES_DIR / conversation.id

    # If isolated container requested, spin up Docker sandbox
    if request.isolated_container:
        try:
            container_id = await sandbox_manager.create_sandbox(
                conversation_id=conversation.id, workspace_dir=workspace_directory
            )
            await update_conversation(conversation.id, container_id=container_id)
            conversation.container_id = container_id
        except Exception as error:
            logger.warning("Failed to create Docker sandbox: %s", error)

    # If initial message provided, add and trigger agent
    if request.initial_message:
        await add_message(
            conversation_id=conversation.id,
            role=MessageRole.USER,
            content=request.initial_message,
        )
        task = asyncio.create_task(_run_agent_turn(conversation.id, request.agent_id))
        _running_agent_tasks[conversation.id] = task

    return conversation


@router.get("", response_model=List[Conversation])
async def get_all_conversations() -> List[Conversation]:
    """Lists all active and stored conversations."""
    return await list_conversations()


@router.get("/{conversation_id}")
async def get_conversation_details(conversation_id: str) -> Dict[str, Any]:
    """Returns conversation metadata and full message history."""
    conversation = await get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = await get_messages(conversation_id)
    return {"conversation": conversation, "messages": messages}


async def _remove_conversation_tree(conversation: Conversation) -> None:
    """Deletes a conversation, its sub-agent conversations, containers, and workspaces."""
    for child in await list_child_conversations(conversation.id):
        await _remove_conversation_tree(child)

    await _cancel_running_turn(conversation.id)

    # Sub-agents share the parent's container, so only tear down a container that
    # this conversation owns.
    if conversation.container_id and not conversation.parent_conversation_id:
        try:
            await sandbox_manager.stop_and_remove_sandbox(conversation.container_id)
        except Exception:
            logger.debug("Sandbox teardown failed for %s", conversation.id, exc_info=True)

    workspace_directory: Path = settings.WORKSPACES_DIR / conversation.id
    if workspace_directory.exists():
        shutil.rmtree(workspace_directory, ignore_errors=True)

    await delete_conversation(conversation.id)


@router.delete("/{conversation_id}")
async def delete_existing_conversation(conversation_id: str) -> Dict[str, str]:
    """Deletes a conversation, cleans up its Docker container, and deletes workspace files."""
    conversation = await get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    await _remove_conversation_tree(conversation)
    return {"status": "deleted"}


@router.post("/{conversation_id}/cancel")
async def cancel_agent_turn(conversation_id: str) -> Dict[str, str]:
    """Cancels an ongoing agent response turn for a conversation."""
    was_running = await _cancel_running_turn(conversation_id)
    await update_conversation(conversation_id, status=ConversationStatus.ACTIVE)

    if was_running:
        return {"status": "cancelled"}

    _broadcast_event(conversation_id, {"type": "cancelled"})
    return {"status": "not_running"}


@router.post("/{conversation_id}/tool-approvals/{call_id}")
async def resolve_tool_approval(
    conversation_id: str, call_id: str, request: ToolApprovalRequest
) -> Dict[str, str]:
    """Records the user's decision for a tool call gated by an `ask_user` permission.

    Raises:
        HTTPException: If no tool call with this id is awaiting a decision.
    """
    if not approval_registry.resolve(call_id, request.approved):
        raise HTTPException(
            status_code=404,
            detail=f"No pending approval for tool call '{call_id}'.",
        )
    return {"status": "approved" if request.approved else "rejected"}


@router.post("/{conversation_id}/messages", response_model=Message)
async def send_user_message(conversation_id: str, request: SendMessageRequest) -> Message:
    """Dispatches a user prompt to a conversation session and triggers an autonomous agent turn."""
    conversation = await get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Wait for the previous turn to finish unwinding before appending, so its cleanup
    # writes land ahead of the new user message.
    await _cancel_running_turn(conversation_id)

    message = await add_message(
        conversation_id=conversation_id,
        role=MessageRole.USER,
        content=request.content,
    )

    _broadcast_event(
        conversation_id,
        {
            "type": "user_message",
            "message": message.model_dump(),
        },
    )

    task = asyncio.create_task(_run_agent_turn(conversation_id, conversation.agent_id))
    _running_agent_tasks[conversation_id] = task

    return message


@router.get("/{conversation_id}/events")
async def stream_conversation_events(conversation_id: str, request: Request) -> StreamingResponse:
    """Server-Sent Events (SSE) stream for real-time conversation updates."""
    conversation = await get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    event_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(
        maxsize=_EVENT_QUEUE_MAXSIZE
    )
    _conversation_event_queues.setdefault(conversation_id, []).append(event_queue)

    def on_task_event(event: Dict[str, Any]) -> None:
        try:
            event_queue.put_nowait(event)
        except asyncio.QueueFull:
            pass

    task_manager.add_listener(conversation_id, on_task_event)

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            yield f"data: {json.dumps({'type': 'connected', 'status': conversation.status.value})}\n\n"

            # Replay active turn buffer if client reconnected during turn
            active_buffer = _active_turn_buffers.get(conversation_id)
            if active_buffer:
                if active_buffer.get("thinking"):
                    yield f"data: {json.dumps({'type': 'thinking', 'content': active_buffer['thinking']})}\n\n"
                if active_buffer.get("tokens"):
                    yield f"data: {json.dumps({'type': 'token', 'content': active_buffer['tokens']})}\n\n"

            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=20.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type': 'ping'})}\n\n"
        finally:
            task_manager.remove_listener(conversation_id, on_task_event)
            listeners = _conversation_event_queues.get(conversation_id)
            if listeners and event_queue in listeners:
                listeners.remove(event_queue)
            if listeners is not None and not listeners:
                _conversation_event_queues.pop(conversation_id, None)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
