from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple
from backend.app.agent.prompt import build_system_prompt
from backend.app.agent.task_manager import task_manager
from backend.app.agents.manager import agent_manager
from backend.app.config import settings
from backend.app.database import (
    add_message,
    get_conversation,
    get_messages,
    update_conversation,
)
from backend.app.llm import generate_completion_stream
from backend.app.models import (
    AgentConfig,
    Conversation,
    ConversationStatus,
    Message,
    MessageRole,
)
from backend.app.tools.registry import tool_registry


@dataclass
class _SessionContext:
    """Encapsulates resolved workspace, agent, and tool configuration for an agent turn."""
    conversation_id: str
    conversation: Conversation
    agent_config: AgentConfig
    workspace_dir: Path
    container_id: Optional[str]
    runtime_context: Dict[str, Any]
    tool_schemas: Optional[List[Dict[str, Any]]]


async def _resolve_session_context(
    conversation_id: str,
    agent_id: Optional[str],
    workspace_dir: Optional[Path],
    container_id: Optional[str],
) -> Tuple[Optional[_SessionContext], Optional[str]]:
    """Resolves and validates the conversation, agent configuration, and runtime environment.

    Returns:
        Tuple of (SessionContext, None) if successful, or (None, error_message) on failure.
    """
    conv = await get_conversation(conversation_id)
    if not conv:
        return None, f"Conversation '{conversation_id}' not found."

    active_agent_id = agent_id or conv.agent_id
    agent_config = agent_manager.get_agent(active_agent_id) or agent_manager.get_agent("general")
    if not agent_config:
        return None, f"Agent configuration '{active_agent_id}' not found."

    resolved_workspace = workspace_dir if workspace_dir else (settings.WORKSPACES_DIR / conversation_id)
    resolved_workspace.mkdir(parents=True, exist_ok=True)

    effective_container_id = container_id if container_id else conv.container_id

    tool_schemas = tool_registry.get_tool_definitions(
        allowed_names=agent_config.allowed_tools if agent_config.allowed_tools else None
    )

    runtime_ctx: Dict[str, Any] = {
        "conversation_id": conversation_id,
        "workspace_dir": resolved_workspace,
        "container_id": effective_container_id,
        "task_manager": task_manager,
    }

    session = _SessionContext(
        conversation_id=conversation_id,
        conversation=conv,
        agent_config=agent_config,
        workspace_dir=resolved_workspace,
        container_id=effective_container_id,
        runtime_context=runtime_ctx,
        tool_schemas=tool_schemas if tool_schemas else None,
    )
    return session, None


def _build_llm_messages(
    raw_messages: List[Message],
    session: _SessionContext,
) -> List[Dict[str, Any]]:
    """Constructs the full prompt message sequence for LiteLLM from database history."""
    system_prompt_text = build_system_prompt(
        agent_config=session.agent_config,
        workspace_dir=session.workspace_dir,
        container_id=session.container_id,
    )

    llm_messages: List[Dict[str, Any]] = [
        {"role": MessageRole.SYSTEM.value, "content": system_prompt_text}
    ]

    for msg in raw_messages:
        role_str = msg.role.value if isinstance(msg.role, MessageRole) else str(msg.role)
        entry: Dict[str, Any] = {"role": role_str}
        if msg.content is not None:
            entry["content"] = msg.content
        if msg.tool_calls:
            entry["tool_calls"] = msg.tool_calls
        if msg.tool_call_id:
            entry["tool_call_id"] = msg.tool_call_id
        if msg.name:
            entry["name"] = msg.name
        llm_messages.append(entry)

    return llm_messages


def _update_tool_delta(
    active_tool_calls: Dict[int, Dict[str, Any]],
    chunk: Dict[str, Any],
    iteration: int,
) -> Dict[str, Any]:
    """Accumulates streaming tool call delta fragments into active_tool_calls state."""
    idx = int(chunk.get("index", 0))
    if idx not in active_tool_calls:
        active_tool_calls[idx] = {
            "id": chunk.get("id") or f"call_{idx}_{iteration}",
            "name": chunk.get("name") or "",
            "arguments": chunk.get("arguments") or "",
        }
    else:
        if chunk.get("id"):
            active_tool_calls[idx]["id"] = chunk.get("id")
        if chunk.get("name"):
            active_tool_calls[idx]["name"] = chunk.get("name")
        if chunk.get("arguments"):
            active_tool_calls[idx]["arguments"] += chunk.get("arguments")

    return {
        "type": "tool_call_delta",
        "index": idx,
        "id": active_tool_calls[idx]["id"],
        "name": active_tool_calls[idx]["name"],
        "arguments": active_tool_calls[idx]["arguments"],
    }


def _finalize_tool_calls(active_tool_calls: Dict[int, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Converts the active tool call delta dict into a list of OpenAI-compatible function calls."""
    if not active_tool_calls:
        return []
    return [
        {
            "id": active_tool_calls[idx]["id"],
            "type": "function",
            "function": {
                "name": active_tool_calls[idx]["name"],
                "arguments": active_tool_calls[idx]["arguments"],
            },
        }
        for idx in sorted(active_tool_calls.keys())
    ]


async def _execute_single_tool(
    tc: Dict[str, Any],
    session: _SessionContext,
) -> Tuple[str, bool]:
    """Executes a single tool call and returns the string output and error flag."""
    fn_name = tc["function"]["name"]
    raw_args = tc["function"]["arguments"]

    try:
        parsed_args = json.loads(raw_args) if raw_args else {}
    except Exception as e:
        return f"Error parsing tool arguments JSON: {str(e)}", True

    result_output = await tool_registry.execute_tool(
        name=fn_name,
        arguments=parsed_args,
        context=session.runtime_context,
    )
    is_error = isinstance(result_output, str) and result_output.startswith("Error:")
    return str(result_output), is_error


class AgentEngine:
    """Core autonomous agent execution loop managing prompt injection, tool dispatching, and streaming."""

    async def run(
        self,
        conversation_id: str,
        agent_id: Optional[str] = None,
        workspace_dir: Optional[Path] = None,
        container_id: Optional[str] = None,
        max_iterations: int = 25,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Runs the ReAct agent turn loop for a conversation, streaming step events and persisting messages.

        Args:
            conversation_id: ID of the conversation.
            agent_id: Optional agent profile override.
            workspace_dir: Optional custom workspace path.
            container_id: Optional Docker container identifier.
            max_iterations: Safety ceiling on tool execution loop turns.

        Yields:
            Dict[str, Any]: SSE-compatible event dictionaries (tokens, tool executions, errors, completion).
        """
        session, error = await _resolve_session_context(
            conversation_id=conversation_id,
            agent_id=agent_id,
            workspace_dir=workspace_dir,
            container_id=container_id,
        )
        if error or not session:
            yield {"type": "error", "error": error}
            return

        await update_conversation(conversation_id, status=ConversationStatus.RUNNING)

        for iteration in range(max_iterations):
            raw_messages = await get_messages(conversation_id)
            llm_messages = _build_llm_messages(raw_messages, session)

            assistant_content = ""
            assistant_thinking = ""
            active_tool_calls: Dict[int, Dict[str, Any]] = {}

            # Stream LLM generation
            async for chunk in generate_completion_stream(
                model=session.agent_config.model,
                messages=llm_messages,
                tools=session.tool_schemas,
                temperature=session.agent_config.temperature,
            ):
                chunk_type = chunk.get("type")

                if chunk_type == "thinking":
                    text = chunk.get("content", "")
                    assistant_thinking += text
                    yield {"type": "thinking", "content": text}

                elif chunk_type == "token":
                    text = chunk.get("content", "")
                    assistant_content += text
                    yield {"type": "token", "content": text}

                elif chunk_type == "tool_call_delta":
                    event = _update_tool_delta(active_tool_calls, chunk, iteration)
                    yield event

                elif chunk_type == "error":
                    yield {"type": "error", "error": chunk.get("error")}
                    await update_conversation(conversation_id, status=ConversationStatus.ACTIVE)
                    return

            final_tool_calls = _finalize_tool_calls(active_tool_calls)

            # Persist assistant turn message
            await add_message(
                conversation_id=conversation_id,
                role=MessageRole.ASSISTANT,
                content=assistant_content if assistant_content else None,
                thinking=assistant_thinking.strip() if assistant_thinking.strip() else None,
                tool_calls=final_tool_calls if final_tool_calls else None,
            )

            # Turn complete if no tool calls requested
            if not final_tool_calls:
                break

            # Execute and persist each tool call
            for tc in final_tool_calls:
                call_id = tc["id"]
                fn_name = tc["function"]["name"]
                raw_args = tc["function"]["arguments"]

                yield {
                    "type": "tool_call_executing",
                    "id": call_id,
                    "name": fn_name,
                    "arguments": raw_args,
                }

                output, is_error = await _execute_single_tool(tc, session)

                yield {
                    "type": "tool_call_result",
                    "id": call_id,
                    "name": fn_name,
                    "output": output,
                    "is_error": is_error,
                }

                await add_message(
                    conversation_id=conversation_id,
                    role=MessageRole.TOOL,
                    content=output,
                    tool_call_id=call_id,
                    name=fn_name,
                )

        await update_conversation(conversation_id, status=ConversationStatus.ACTIVE)
        yield {"type": "done"}


agent_engine = AgentEngine()
