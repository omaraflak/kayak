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
    # Import agent runner dynamically to avoid circular import
    from backend.app.agent.engine import agent_engine

    parent_id = conversation_id or "root"
    title = f"SubAgent: {prompt[:30]}..."

    # Create child conversation sharing workspace
    child_conv = await create_conversation(
        title=title,
        agent_id=agent_id,
        isolated_container=bool(container_id),
    )

    # If container is active, attach child conversation to the same container
    if container_id:
        child_conv.container_id = container_id

    # Add initial user prompt
    await add_message(
        conversation_id=child_conv.id,
        role="user",
        content=prompt,
    )

    # Register as background task
    task = await create_task(
        conversation_id=parent_id,
        task_type="subagent",
        name=f"SubAgent [{agent_id}]",
        command=prompt,
    )

    async def _execute_subagent():
        try:
            task_manager.notify_listeners(
                parent_id,
                {
                    "type": "subagent_started",
                    "task_id": task.id,
                    "subagent_conversation_id": child_conv.id,
                    "agent_id": agent_id,
                },
            )

            final_response = ""
            async for event in agent_engine.run(
                conversation_id=child_conv.id,
                agent_id=agent_id,
                workspace_dir=workspace_dir,
                container_id=container_id,
            ):
                if event.get("type") == "token":
                    final_response += event.get("content", "")

            await update_task(task.id, status="completed", stdout=final_response)
            task_manager.notify_listeners(
                parent_id,
                {
                    "type": "subagent_finished",
                    "task_id": task.id,
                    "subagent_conversation_id": child_conv.id,
                    "status": "completed",
                    "result": final_response,
                },
            )
            return final_response
        except Exception as e:
            await update_task(task.id, status="failed", stderr=str(e))
            task_manager.notify_listeners(
                parent_id,
                {
                    "type": "subagent_finished",
                    "task_id": task.id,
                    "subagent_conversation_id": child_conv.id,
                    "status": "failed",
                    "error": str(e),
                },
            )
            return f"Error executing sub-agent: {str(e)}"

    if wait_for_completion:
        result = await _execute_subagent()
        return (
            f"=== SubAgent [{agent_id}] Finished ===\nConversation ID:"
            f" {child_conv.id}\n\n{result}"
        )
    else:
        asyncio.create_task(_execute_subagent())
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
        output.append(f"[{msg.role.upper()}]: {msg.content}")

    return "\n\n".join(output)
