import asyncio
from pathlib import Path
from typing import Optional
from backend.app.agent.task_manager import task_manager
from backend.app.database import (
    add_message,
    create_conversation,
    create_task,
    get_conversation,
    get_messages,
    update_task,
)
from backend.app.models import TaskStatus


async def _run_subagent_loop(
    child_conv_id: str,
    agent_id: str,
    task_id: str,
    parent_id: str,
    workspace_dir: Optional[Path],
    container_id: Optional[str],
) -> str:
    """Executes the subagent engine loop and broadcasts start/finish events."""
    from backend.app.agent.engine import agent_engine

    task_manager.notify_listeners(
        parent_id,
        {
            "type": "subagent_started",
            "task_id": task_id,
            "subagent_conversation_id": child_conv_id,
            "agent_id": agent_id,
        },
    )

    final_response = ""
    try:
        async for event in agent_engine.run(
            conversation_id=child_conv_id,
            agent_id=agent_id,
            workspace_dir=workspace_dir,
            container_id=container_id,
        ):
            if event.get("type") == "token":
                final_response += event.get("content", "")

        await update_task(task_id, status=TaskStatus.COMPLETED, stdout=final_response)
        task_manager.notify_listeners(
            parent_id,
            {
                "type": "subagent_finished",
                "task_id": task_id,
                "subagent_conversation_id": child_conv_id,
                "status": "completed",
                "result": final_response,
            },
        )
        return final_response
    except Exception as e:
        await update_task(task_id, status=TaskStatus.FAILED, stderr=str(e))
        task_manager.notify_listeners(
            parent_id,
            {
                "type": "subagent_finished",
                "task_id": task_id,
                "subagent_conversation_id": child_conv_id,
                "status": "failed",
                "error": str(e),
            },
        )
        return f"Error executing sub-agent: {str(e)}"


async def spawn_subagent(
    agent_id: str,
    prompt: str,
    wait_for_completion: bool = True,
    conversation_id: Optional[str] = None,
    workspace_dir: Optional[Path] = None,
    container_id: Optional[str] = None,
) -> str:
    """Spawns an autonomous sub-agent to handle a focused sub-task.

    Args:
        agent_id: The ID of the agent configuration profile to use (e.g. 'coding', 'researcher', 'general').
        prompt: The specific task or prompt instruction for the sub-agent.
        wait_for_completion: If True, waits for the sub-agent to finish and returns the answer. If False, runs in background and returns task ID.
    """
    parent_id = conversation_id or "root"
    title = f"SubAgent: {prompt[:30]}..."

    child_conv = await create_conversation(
        title=title,
        agent_id=agent_id,
        isolated_container=bool(container_id),
    )

    if container_id:
        child_conv.container_id = container_id

    await add_message(
        conversation_id=child_conv.id,
        role="user",
        content=prompt,
    )

    task = await create_task(
        conversation_id=parent_id,
        task_type="subagent",
        name=f"SubAgent [{agent_id}]",
        command=prompt,
    )

    if wait_for_completion:
        result = await _run_subagent_loop(
            child_conv_id=child_conv.id,
            agent_id=agent_id,
            task_id=task.id,
            parent_id=parent_id,
            workspace_dir=workspace_dir,
            container_id=container_id,
        )
        return (
            f"=== SubAgent [{agent_id}] Finished ===\nConversation ID:"
            f" {child_conv.id}\n\n{result}"
        )
    else:
        asyncio.create_task(
            _run_subagent_loop(
                child_conv_id=child_conv.id,
                agent_id=agent_id,
                task_id=task.id,
                parent_id=parent_id,
                workspace_dir=workspace_dir,
                container_id=container_id,
            )
        )
        return (
            f"SubAgent [{agent_id}] spawned in background.\nTask ID:"
            f" {task.id}\nConversation ID: {child_conv.id}\nUse"
            " `get_task_status(task_id)` to monitor progress."
        )


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
