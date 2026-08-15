import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Set
from backend.app.agent.events import subagent_finished, subagent_started
from backend.app.agent.task_manager import task_manager
from backend.app.agents.manager import agent_manager, allowed_subagent_ids
from backend.app.config import settings
from backend.app.database import (
    add_message,
    create_conversation,
    create_task,
    get_conversation,
    get_messages,
    update_task,
)
from backend.app.models import TaskStatus
from backend.app.models import ToolCategory, ToolRisk
from backend.app.tools.metadata import tool_metadata

logger = logging.getLogger(__name__)

# Detached sub-agent runs are held here so the event loop cannot collect them
# mid-execution; asyncio only keeps weak references to running tasks.
_detached_subagent_tasks: Set[asyncio.Task[Any]] = set()


async def _run_subagent_loop(
    child_conv_id: str,
    agent_id: str,
    task_id: str,
    parent_id: str,
    workspace_dir: Optional[Path],
    container_id: Optional[str],
    depth: int,
) -> str:
    """Executes the subagent engine loop and broadcasts start/finish events."""
    from backend.app.agent.engine import agent_engine

    task_manager.notify_listeners(
        parent_id, subagent_started(task_id, child_conv_id, agent_id)
    )

    final_response = ""
    try:
        async for event in agent_engine.run(
            conversation_id=child_conv_id,
            agent_id=agent_id,
            workspace_dir=workspace_dir,
            container_id=container_id,
            depth=depth,
        ):
            if event.get("type") == "token":
                final_response += event.get("content", "")
            elif event.get("type") == "error":
                final_response += f"\n[Sub-agent error: {event.get('error')}]"

        if not final_response.strip():
            # A sub-agent that ended on a tool call rather than prose still has a
            # result worth returning; fall back to its last assistant message.
            final_response = await _last_assistant_text(child_conv_id)

        await update_task(task_id, status=TaskStatus.COMPLETED, stdout=final_response)
        task_manager.notify_listeners(
            parent_id,
            subagent_finished(task_id, child_conv_id, "completed", result=final_response),
        )
        return final_response
    except asyncio.CancelledError:
        await update_task(task_id, status=TaskStatus.STOPPED)
        raise
    except Exception as e:
        logger.exception("Sub-agent run failed for conversation %s", child_conv_id)
        await update_task(task_id, status=TaskStatus.FAILED, stderr=str(e))
        task_manager.notify_listeners(
            parent_id,
            subagent_finished(task_id, child_conv_id, "failed", error=str(e)),
        )
        return f"Error executing sub-agent: {str(e)}"


async def _last_assistant_text(conversation_id: str) -> str:
    """Returns the most recent assistant prose from a conversation, if any."""
    for msg in reversed(await get_messages(conversation_id)):
        if msg.role.value == "assistant" and msg.content:
            return msg.content
    return "[Sub-agent produced no textual output.]"


@tool_metadata(category=ToolCategory.ORCHESTRATION, risk=ToolRisk.MODERATE)
async def spawn_subagent(
    agent_id: str,
    prompt: str,
    wait_for_completion: bool = True,
    conversation_id: Optional[str] = None,
    workspace_dir: Optional[Path] = None,
    container_id: Optional[str] = None,
    agent_depth: int = 0,
    caller_agent_id: Optional[str] = None,
) -> str:
    """Spawns an autonomous sub-agent to handle a focused sub-task.

    Args:
        agent_id: The ID of the agent configuration profile to use. You may only use profiles your own configuration allows.
        prompt: The specific task or prompt instruction for the sub-agent.
        wait_for_completion: If True, waits for the sub-agent to finish and returns the answer. If False, runs in background and returns task ID.
    """
    if not conversation_id:
        return "Error: No active conversation context found."

    # Without a ceiling a sub-agent can spawn sub-agents indefinitely, fanning out
    # into unbounded model spend from a single user message.
    if agent_depth >= settings.AGENT_MAX_SUBAGENT_DEPTH:
        return (
            f"Error: Maximum sub-agent nesting depth ({settings.AGENT_MAX_SUBAGENT_DEPTH})"
            " reached. Complete this work yourself rather than delegating further."
        )

    # The allowlist is what keeps delegation from being an escape hatch: an agent
    # restricted to safe tools must not be able to act through a profile that has
    # dangerous ones. The caller's id comes from the engine, not from the model.
    caller_config = agent_manager.get_agent(caller_agent_id) if caller_agent_id else None
    if caller_config is not None:
        allowed = allowed_subagent_ids(caller_config)
        if agent_id not in allowed:
            allowed_list = ", ".join(sorted(allowed)) if allowed else "none"
            return (
                f"Error: Your profile '{caller_config.id}' is not allowed to start"
                f" '{agent_id}' sub-agents. Allowed profiles: {allowed_list}."
            )

    if not agent_manager.get_agent(agent_id):
        return f"Error: Agent profile '{agent_id}' does not exist."

    title = f"SubAgent: {prompt[:30]}..."

    child_conv = await create_conversation(
        title=title,
        agent_id=agent_id,
        isolated_container=bool(container_id),
        # Persisted rather than set in memory: a child whose container is only known
        # to the running task loses its sandbox the moment it is reopened or resumed.
        container_id=container_id,
        parent_conversation_id=conversation_id,
    )

    await add_message(
        conversation_id=child_conv.id,
        role="user",
        content=prompt,
    )

    task = await create_task(
        conversation_id=conversation_id,
        task_type="subagent",
        name=f"SubAgent [{agent_id}]",
        command=prompt,
        subagent_conversation_id=child_conv.id,
    )

    run_kwargs: Dict[str, Any] = {
        "child_conv_id": child_conv.id,
        "agent_id": agent_id,
        "task_id": task.id,
        "parent_id": conversation_id,
        "workspace_dir": workspace_dir,
        "container_id": container_id,
        "depth": agent_depth + 1,
    }

    if wait_for_completion:
        result = await _run_subagent_loop(**run_kwargs)
        return (
            f"=== SubAgent [{agent_id}] Finished ===\nConversation ID:"
            f" {child_conv.id}\n\n{result}"
        )

    detached = asyncio.create_task(_run_subagent_loop(**run_kwargs))
    _detached_subagent_tasks.add(detached)
    detached.add_done_callback(_detached_subagent_tasks.discard)
    # Registered so stopping the task from the UI actually reaches the run.
    task_manager.register_run(task.id, detached)

    return (
        f"SubAgent [{agent_id}] spawned in background.\nTask ID:"
        f" {task.id}\nConversation ID: {child_conv.id}\nUse"
        " `get_task_status(task_id)` to monitor progress."
    )


@tool_metadata(category=ToolCategory.ORCHESTRATION, risk=ToolRisk.LOW)
async def get_subagent_result(
    subagent_conversation_id: str,
) -> str:
    """Retrieves the latest messages and findings from a sub-agent conversation.

    Args:
        subagent_conversation_id: The conversation ID of the spawned sub-agent.
    """
    conv = await get_conversation(subagent_conversation_id)
    if not conv:
        return f"Error: Sub-agent conversation '{subagent_conversation_id}' not found."

    messages = await get_messages(subagent_conversation_id)
    output = [
        f"=== SubAgent Conversation: {conv.title} (Agent: {conv.agent_id}) ==="
    ]
    for msg in messages:
        role_label = (msg.role.value if hasattr(msg.role, "value") else str(msg.role)).upper()
        output.append(f"[{role_label}]: {msg.content}")

    return "\n\n".join(output)
