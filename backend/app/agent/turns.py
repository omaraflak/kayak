"""Running one conversation's turn, and stopping it.

A turn outlives the request that asked for it: the HTTP call returns as soon as the
work is scheduled, and everything after that reaches the user over the event stream.
This module owns that lifetime -- what is running, how it is cancelled, and how its
failures are reported -- so the route layer only has to decide when to start one.
"""

import asyncio
import logging
from typing import Dict

from backend.app.agent import events
from backend.app.agent.approvals import approval_registry
from backend.app.agent.engine import agent_engine
from backend.app.agent.sandbox import sandbox_manager
from backend.app.config import settings
from backend.app.database import get_conversation, update_conversation
from backend.app.models import Conversation, ConversationStatus
from backend.app.realtime import conversation_events

logger = logging.getLogger(__name__)

SANDBOX_UNAVAILABLE_DETAIL = (
    "Could not start this conversation's isolated container. Kayak runs every"
    " agent inside Docker, so Docker must be installed and running."
)

#: The turn currently running for each conversation.
_running_turns: Dict[str, asyncio.Task[None]] = {}


def is_running(conversation_id: str) -> bool:
    """Whether a turn is in flight for this conversation."""
    task = _running_turns.get(conversation_id)
    return task is not None and not task.done()


async def ensure_sandbox(conversation: Conversation) -> str:
    """Makes sure the conversation's Docker container exists and is running.

    Every conversation executes its tools inside a container; there is no host
    fallback. The container can be missing even for an old conversation -- Docker
    restarted, a manual prune, or a conversation created before containers became
    mandatory -- so this runs before every turn, not only at creation.

    A recorded container is revived rather than replaced: sub-agent conversations
    share their parent's container, and replacing it would split them apart.

    Raises:
        RuntimeError: If Docker is unavailable or the container cannot start.
    """
    if conversation.container_id and await sandbox_manager.ensure_running(
        conversation.container_id
    ):
        return conversation.container_id

    container_id = await sandbox_manager.create_sandbox(
        conversation_id=conversation.id,
        workspace_dir=settings.WORKSPACES_DIR / conversation.id,
    )
    if container_id != conversation.container_id:
        await update_conversation(conversation.id, container_id=container_id)
        conversation.container_id = container_id
    return container_id


def start(conversation_id: str, agent_id: str) -> None:
    """Schedules a turn and returns immediately.

    The task is retained: asyncio holds only weak references to running tasks, so a
    fire-and-forget turn can be collected mid-flight.
    """
    task = asyncio.create_task(_run(conversation_id, agent_id))
    _running_turns[conversation_id] = task


async def _run(conversation_id: str, agent_id: str) -> None:
    """Runs the agent loop, publishing every event it produces."""
    try:
        conversation = await get_conversation(conversation_id)
        if not conversation:
            return

        if not await _prepare_container(conversation):
            return

        conversation_events.begin_turn(conversation_id)
        async for event in agent_engine.run(
            conversation_id=conversation_id,
            agent_id=agent_id,
            workspace_dir=settings.WORKSPACES_DIR / conversation_id,
            container_id=conversation.container_id,
        ):
            conversation_events.publish(conversation_id, event)
    except asyncio.CancelledError:
        await update_conversation(conversation_id, status=ConversationStatus.ACTIVE)
        conversation_events.publish(conversation_id, events.cancelled())
        raise
    except Exception as error:
        logger.exception("Agent turn failed for conversation %s", conversation_id)
        await update_conversation(conversation_id, status=ConversationStatus.ACTIVE)
        conversation_events.publish(conversation_id, events.error(str(error)))
    finally:
        conversation_events.end_turn(conversation_id)
        _running_turns.pop(conversation_id, None)


async def _prepare_container(conversation: Conversation) -> bool:
    """Brings up the conversation's container, reporting failure to the client.

    Returns:
        bool: True if the turn can proceed.
    """
    try:
        await ensure_sandbox(conversation)
        return True
    except Exception as error:
        logger.warning(
            "Could not provide a sandbox for conversation %s: %s",
            conversation.id,
            error,
        )
        await update_conversation(conversation.id, status=ConversationStatus.ACTIVE)
        conversation_events.publish(
            conversation.id, events.error(f"{SANDBOX_UNAVAILABLE_DETAIL} ({error})")
        )
        return False


async def cancel(conversation_id: str) -> bool:
    """Stops an in-flight turn and waits for its cleanup to finish.

    Awaiting the cancelled task matters: the engine writes placeholder results for
    tool calls that never ran, and starting the next turn before that lands would
    read a history with dangling tool calls.

    Returns:
        bool: True if a turn was actually running.
    """
    # Unblock the engine if it is parked waiting on a tool approval.
    approval_registry.cancel_conversation(conversation_id)

    task = _running_turns.get(conversation_id)
    if not task or task.done():
        return False

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        # The turn's cancellation, not ours: swallowing it here is the point.
        pass
    except Exception:
        logger.debug("Turn for %s failed while unwinding", conversation_id, exc_info=True)
    return True


async def cancel_all() -> None:
    """Stops every in-flight turn, for server shutdown."""
    for conversation_id in list(_running_turns.keys()):
        await cancel(conversation_id)

