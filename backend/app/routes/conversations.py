"""HTTP surface for conversations.

Everything here is request handling: validate, call into the turn runner or the
database, answer. The turn's own lifetime lives in `agent.turns`, and the live event
fan-out in `realtime`, so that neither is entangled with FastAPI.
"""

import asyncio
import json
import logging
from pathlib import Path
import shutil
from typing import Any, AsyncGenerator, Dict, List, Optional, Set

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.app.agent import events, turns
from backend.app.agent.approvals import approval_registry
from backend.app.agent.events import ConversationEvent
from backend.app.agent.sandbox import sandbox_manager
from backend.app.agent.task_manager import task_manager
from backend.app.agent.turns import SANDBOX_UNAVAILABLE_DETAIL, ensure_sandbox
from backend.app.agents.manager import agent_manager
from backend.app.config import settings
from backend.app.database import (
    add_message,
    copy_messages_through,
    create_conversation,
    delete_conversation,
    delete_messages_from,
    get_conversation,
    get_messages,
    get_preceding_user_message,
    list_child_conversations,
    list_conversations,
    update_conversation,
)
from backend.app.llm import generate_title, missing_key_error
from backend.app.models import (
    Conversation,
    ConversationStatus,
    CreateConversationRequest,
    Message,
    MessageRole,
    SendMessageRequest,
)
from backend.app.realtime import conversation_events

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/conversations", tags=["conversations"])

#: How long an idle event stream waits before sending a keep-alive.
_STREAM_PING_SECONDS = 20.0

# Fire-and-forget work (title generation) held so it is not garbage collected.
_side_tasks: Set[asyncio.Task[None]] = set()


class ToolApprovalRequest(BaseModel):
    """Payload recording the user's decision on a gated tool call."""
    approved: bool


class MessageAnchorRequest(BaseModel):
    """Identifies the message a revert, retry, or branch is anchored on."""
    message_id: str


def _spawn_side_task(coroutine: "asyncio.Future[None] | Any") -> None:
    """Schedules background work and keeps a reference until it completes."""
    task = asyncio.ensure_future(coroutine)
    _side_tasks.add(task)
    task.add_done_callback(_side_tasks.discard)


# --------------------------------------------------------------------- helpers


def _clean_initial_title(prompt: Optional[str]) -> Optional[str]:
    """Derives a provisional title from the first words of an initial message."""
    if not prompt:
        return None
    clean = " ".join(prompt.strip().split())
    return clean[:36].rsplit(" ", 1)[0] + "..." if len(clean) > 36 else clean


def resolve_conversation_model(agent_id: str) -> Optional[str]:
    """Returns the model a conversation with this agent will actually run on.

    Resolved exactly the way the engine resolves it, falling back to the general agent,
    so a generated title comes from the same model that answers the conversation. There
    is no global default to reach for: if no agent resolves, the caller does without.
    """
    agent_config = agent_manager.get_agent(agent_id) or agent_manager.get_agent("general")
    return agent_config.model if agent_config else None


def require_provider_key(agent_id: str) -> None:
    """Refuses a turn whose model has no stored credential, before anything is created.

    The engine reports this too, but only as a stream event, and this failure is
    instant: on a fresh install the turn is over before the browser has finished
    subscribing, so the event went nowhere and the user was left looking at a prompt
    that never got an answer. Refusing the request instead puts the reason in the
    response, where both the composer and the new-conversation screen already show it.

    Raises:
        HTTPException: 400 naming the provider whose key is missing.
    """
    model = resolve_conversation_model(agent_id)
    if not model:
        return

    message = missing_key_error(model)
    if message:
        raise HTTPException(status_code=400, detail=message)


async def _generate_title(conversation_id: str, prompt: str, model_name: str) -> None:
    """Retitles a conversation from its opening prompt, in the background."""
    try:
        generated = await generate_title(prompt, model=model_name)
        if generated:
            await update_conversation(conversation_id, title=generated)
            conversation_events.publish(conversation_id, events.title_updated(generated))
    except Exception:
        logger.debug("Title generation failed for %s", conversation_id, exc_info=True)


def _schedule_title(conversation: Conversation, prompt: Optional[str], had_title: bool) -> None:
    """Starts title generation when the conversation has nothing better to be called."""
    model = resolve_conversation_model(conversation.agent_id)
    if had_title or not prompt or not model:
        return
    _spawn_side_task(_generate_title(conversation.id, prompt, model))


async def _load_conversation(conversation_id: str) -> Conversation:
    """Fetches a conversation or reports that it does not exist.

    Raises:
        HTTPException: 404 if there is no such conversation.
    """
    conversation = await get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


async def _load_conversation_and_anchor(
    conversation_id: str, message_id: str
) -> Conversation:
    """Validates that a conversation exists and that the anchor message belongs to it.

    Raises:
        HTTPException: 404 if either is missing.
    """
    conversation = await _load_conversation(conversation_id)

    messages = await get_messages(conversation_id)
    if not any(message.id == message_id for message in messages):
        raise HTTPException(
            status_code=404,
            detail="That message is not part of this conversation.",
        )
    return conversation


# ---------------------------------------------------------------------- routes


@router.post("", response_model=Conversation)
async def create_new_conversation(request: CreateConversationRequest) -> Conversation:
    """Creates a new conversation bound to its own isolated Docker container.

    Raises:
        HTTPException: 400 when the agent's provider has no key, checked before
            anything is created; 503 when the container cannot be started. Failing
            rather than falling back to the host is deliberate: running tools outside
            a container would silently drop the isolation the platform promises.
    """
    if request.initial_message:
        require_provider_key(request.agent_id)

    title = request.title or _clean_initial_title(request.initial_message)

    conversation = await create_conversation(
        title=title or "New Conversation",
        agent_id=request.agent_id,
        isolated_container=True,
    )

    try:
        await ensure_sandbox(conversation)
    except Exception as error:
        logger.warning("Failed to create Docker sandbox: %s", error)
        await delete_conversation(conversation.id)
        raise HTTPException(
            status_code=503, detail=f"{SANDBOX_UNAVAILABLE_DETAIL} ({error})"
        )

    _schedule_title(conversation, request.initial_message, had_title=bool(request.title))

    if request.initial_message:
        await add_message(
            conversation_id=conversation.id,
            role=MessageRole.USER,
            content=request.initial_message,
        )
        turns.start(conversation.id, request.agent_id)

    return conversation


@router.get("", response_model=List[Conversation])
async def get_all_conversations() -> List[Conversation]:
    """Lists all active and stored conversations."""
    return await list_conversations()


@router.get("/{conversation_id}")
async def get_conversation_details(conversation_id: str) -> Dict[str, Any]:
    """Returns conversation metadata and full message history."""
    conversation = await _load_conversation(conversation_id)
    return {
        "conversation": conversation,
        "messages": await get_messages(conversation_id),
    }


@router.delete("/{conversation_id}")
async def delete_existing_conversation(conversation_id: str) -> Dict[str, str]:
    """Deletes a conversation, its sub-agents, its container, and its workspace."""
    conversation = await _load_conversation(conversation_id)
    await _remove_conversation_tree(conversation)
    return {"status": "deleted"}


async def _remove_conversation_tree(conversation: Conversation) -> None:
    """Deletes a conversation, its sub-agent conversations, containers, and workspaces."""
    for child in await list_child_conversations(conversation.id):
        await _remove_conversation_tree(child)

    await turns.cancel(conversation.id)

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


@router.post("/{conversation_id}/cancel")
async def cancel_agent_turn(conversation_id: str) -> Dict[str, str]:
    """Stops an ongoing agent turn."""
    was_running = await turns.cancel(conversation_id)
    await update_conversation(conversation_id, status=ConversationStatus.ACTIVE)

    if was_running:
        return {"status": "cancelled"}

    # Nothing was running, but a client may still think otherwise; telling it so is
    # what releases a composer stuck on a turn that ended without being seen.
    conversation_events.publish(conversation_id, events.cancelled())
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
async def send_user_message(
    conversation_id: str, request: SendMessageRequest
) -> Message:
    """Appends a user prompt and starts the agent's turn."""
    conversation = await _load_conversation(conversation_id)

    # Before the message is stored: the composer puts a rejected message back for the
    # user to resend once the key is in place.
    require_provider_key(conversation.agent_id)

    # Wait for the previous turn to finish unwinding before appending, so its cleanup
    # writes land ahead of the new user message.
    await turns.cancel(conversation_id)

    message = await add_message(
        conversation_id=conversation_id,
        role=MessageRole.USER,
        content=request.content,
    )
    conversation_events.publish(
        conversation_id, events.user_message(message.model_dump())
    )

    turns.start(conversation_id, conversation.agent_id)
    return message


@router.post("/{conversation_id}/revert")
async def revert_to_message(
    conversation_id: str, request: MessageAnchorRequest
) -> Dict[str, Any]:
    """Removes a turn and everything after it, returning the prompt that started it.

    The anchor is the first message of an agent turn. Reverting deletes that turn and
    every later message, along with the user message that prompted it, which is handed
    back so the composer can be repopulated for editing.
    """
    await _load_conversation_and_anchor(conversation_id, request.message_id)

    # Cancel first: the engine writes placeholder results for tool calls that never
    # ran, and those writes must land before the history is truncated.
    await turns.cancel(conversation_id)

    prompt_message = await get_preceding_user_message(conversation_id, request.message_id)
    truncate_from = prompt_message.id if prompt_message else request.message_id

    removed = await delete_messages_from(conversation_id, truncate_from)
    await update_conversation(conversation_id, status=ConversationStatus.ACTIVE)
    conversation_events.publish(conversation_id, events.history_changed())

    return {
        "status": "reverted",
        "removed": removed,
        "prompt": prompt_message.content if prompt_message else None,
    }


@router.post("/{conversation_id}/retry")
async def retry_from_message(
    conversation_id: str, request: MessageAnchorRequest
) -> Dict[str, str]:
    """Discards a turn and generates it again from the same history."""
    conversation = await _load_conversation_and_anchor(conversation_id, request.message_id)

    # Checked before the turn is deleted: a retry that cannot run must not destroy the
    # answer it was going to replace.
    require_provider_key(conversation.agent_id)

    await turns.cancel(conversation_id)

    removed = await delete_messages_from(conversation_id, request.message_id)
    if not removed:
        raise HTTPException(status_code=409, detail="Nothing to retry from that message.")

    conversation_events.publish(conversation_id, events.history_changed())
    turns.start(conversation_id, conversation.agent_id)
    return {"status": "running"}


@router.post("/{conversation_id}/branch", response_model=Conversation)
async def branch_from_message(
    conversation_id: str, request: MessageAnchorRequest
) -> Conversation:
    """Copies this conversation up to a message into a new one to continue differently.

    The anchor is the last message of the turn being branched at, so the copied history
    ends on a complete turn -- a prefix cut mid-turn would leave a tool call with no
    result, which providers reject.
    """
    source = await _load_conversation_and_anchor(conversation_id, request.message_id)

    branch = await create_conversation(
        title=source.title,
        agent_id=source.agent_id,
        isolated_container=True,
        branched_from_conversation_id=source.id,
        branched_from_message_id=request.message_id,
    )

    copied = await copy_messages_through(conversation_id, branch.id, request.message_id)
    if not copied:
        await delete_conversation(branch.id)
        raise HTTPException(status_code=409, detail="Could not copy the conversation history.")

    # The transcript refers to files the agent made; a branch that cannot see them
    # would be reasoning about a workspace that does not exist.
    await _copy_workspace(conversation_id, branch.id)

    try:
        await ensure_sandbox(branch)
    except Exception as error:
        logger.warning("Failed to create sandbox for branch %s: %s", branch.id, error)
        await delete_conversation(branch.id)
        raise HTTPException(
            status_code=503, detail=f"{SANDBOX_UNAVAILABLE_DETAIL} ({error})"
        )

    return branch


async def _copy_workspace(source_conversation_id: str, target_conversation_id: str) -> None:
    """Duplicates a conversation's workspace directory into the branch's own."""
    source_dir: Path = settings.WORKSPACES_DIR / source_conversation_id
    if not source_dir.is_dir():
        return

    target_dir: Path = settings.WORKSPACES_DIR / target_conversation_id

    def _copy() -> None:
        shutil.copytree(source_dir, target_dir, dirs_exist_ok=True, symlinks=True)

    try:
        # A workspace can hold a build tree; copying it must not block the event loop.
        await asyncio.get_running_loop().run_in_executor(None, _copy)
    except Exception:
        logger.warning(
            "Could not copy workspace for branch %s", target_conversation_id, exc_info=True
        )


# ----------------------------------------------------------------- event stream


def _sse(event: ConversationEvent) -> str:
    """Formats one event as an SSE frame."""
    return f"data: {json.dumps(event)}\n\n"


def _catch_up(conversation_id: str, status: str) -> List[ConversationEvent]:
    """Everything a client needs on connecting to be level with the conversation.

    A tab that reconnects mid-turn, or opens one that a different tab started, has to
    be told what it missed: how much of the answer exists, which tools have run, and
    whether the turn is parked on an approval it never saw announced.
    """
    caught_up: List[ConversationEvent] = [
        # `is_running` is what restores the composer; the stored status alone lags a
        # turn that is finishing as we connect.
        events.connected(status, turns.is_running(conversation_id))
    ]

    replay = conversation_events.replay_for(conversation_id)
    if replay is not None:
        caught_up.extend(replay.events())

    for approval in approval_registry.list_for_conversation(conversation_id):
        caught_up.append(
            events.tool_approval_required(
                approval["id"], approval["name"], approval["arguments"]
            )
        )
    return caught_up


@router.get("/{conversation_id}/events")
async def stream_conversation_events(
    conversation_id: str, request: Request
) -> StreamingResponse:
    """Server-Sent Events stream of everything happening in one conversation."""
    conversation = await _load_conversation(conversation_id)

    queue = conversation_events.subscribe(conversation_id)

    def on_task_event(event: ConversationEvent) -> None:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            pass

    task_manager.add_listener(conversation_id, on_task_event)

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            for event in _catch_up(conversation_id, conversation.status.value):
                yield _sse(event)

            while not await request.is_disconnected():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=_STREAM_PING_SECONDS)
                    yield _sse(event)
                except asyncio.TimeoutError:
                    yield _sse(events.ping())
        finally:
            task_manager.remove_listener(conversation_id, on_task_event)
            conversation_events.unsubscribe(conversation_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
