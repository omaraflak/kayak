import asyncio
import json
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional
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
        conv = await get_conversation(conversation_id)
        if not conv:
            yield {
                "type": "error",
                "error": f"Conversation '{conversation_id}' not found.",
            }
            return

        active_agent_id = agent_id or conv.agent_id
        agent_config = agent_manager.get_agent(active_agent_id)
        if not agent_config:
            # Fallback to general agent
            agent_config = agent_manager.get_agent("general")

        if not agent_config:
            yield {
                "type": "error",
                "error": f"Agent configuration '{active_agent_id}' not found.",
            }
            return

        # Resolve workspace directory
        resolved_workspace = (
            workspace_dir
            if workspace_dir
            else settings.WORKSPACES_DIR / conversation_id
        )
        resolved_workspace.mkdir(parents=True, exist_ok=True)

        effective_container_id = (
            container_id if container_id else conv.container_id
        )

        # Build tools schema
        tool_schemas = tool_registry.get_tool_definitions(
            allowed_names=agent_config.allowed_tools
            if agent_config.allowed_tools
            else None
        )

        # Context for tool execution
        runtime_context: Dict[str, Any] = {
            "conversation_id": conversation_id,
            "workspace_dir": resolved_workspace,
            "container_id": effective_container_id,
            "task_manager": task_manager,
        }

        # Update conversation status
        await update_conversation(conversation_id, status=ConversationStatus.RUNNING)

        # Multi-turn ReAct loop
        for iteration in range(max_iterations):
            # Fetch latest history from database
            raw_messages = await get_messages(conversation_id)

            # Build system prompt
            system_prompt_text = build_system_prompt(
                agent_config=agent_config,
                workspace_dir=resolved_workspace,
                container_id=effective_container_id,
            )

            llm_messages: List[Dict[str, Any]] = [
                {"role": MessageRole.SYSTEM.value, "content": system_prompt_text}
            ]

            for msg in raw_messages:
                m: Dict[str, Any] = {"role": msg.role.value if isinstance(msg.role, MessageRole) else str(msg.role)}
                if msg.content is not None:
                    m["content"] = msg.content
                if msg.tool_calls:
                    m["tool_calls"] = msg.tool_calls
                if msg.tool_call_id:
                    m["tool_call_id"] = msg.tool_call_id
                if msg.name:
                    m["name"] = msg.name
                llm_messages.append(m)

            assistant_content = ""
            assistant_thinking = ""
            active_tool_calls: Dict[int, Dict[str, Any]] = {}

            # Stream LLM response
            async for chunk in generate_completion_stream(
                model=agent_config.model,
                messages=llm_messages,
                tools=tool_schemas if tool_schemas else None,
                temperature=agent_config.temperature,
            ):
                chunk_type = chunk.get("type")

                if chunk_type == "thinking":
                    content = chunk.get("content", "")
                    assistant_thinking += content
                    yield {"type": "thinking", "content": content}

                elif chunk_type == "token":
                    content = chunk.get("content", "")
                    assistant_content += content
                    yield {"type": "token", "content": content}

                elif chunk_type == "tool_call_delta":
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
                            active_tool_calls[idx]["arguments"] += chunk.get(
                                "arguments"
                            )

                    yield {
                        "type": "tool_call_delta",
                        "index": idx,
                        "id": active_tool_calls[idx]["id"],
                        "name": active_tool_calls[idx]["name"],
                        "arguments": active_tool_calls[idx]["arguments"],
                    }

                elif chunk_type == "error":
                    yield {"type": "error", "error": chunk.get("error")}
                    await update_conversation(conversation_id, status=ConversationStatus.ACTIVE)
                    return

            # Construct finalized tool calls list
            final_tool_calls: List[Dict[str, Any]] = []
            if active_tool_calls:
                for idx in sorted(active_tool_calls.keys()):
                    tc = active_tool_calls[idx]
                    final_tool_calls.append(
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": tc["arguments"],
                            },
                        }
                    )

            # Persist assistant message in DB
            await add_message(
                conversation_id=conversation_id,
                role=MessageRole.ASSISTANT,
                content=assistant_content if assistant_content else None,
                thinking=assistant_thinking.strip() if assistant_thinking.strip() else None,
                tool_calls=final_tool_calls if final_tool_calls else None,
            )

            # If no tools were called, the turn is finished!
            if not final_tool_calls:
                break

            # Execute each requested tool call
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

                # Parse JSON arguments safely
                try:
                    parsed_args = json.loads(raw_args) if raw_args else {}
                except Exception as e:
                    parsed_args = {}
                    result_output = (
                        f"Error parsing tool arguments JSON: {str(e)}"
                    )
                    is_error = True
                else:
                    is_error = False
                    result_output = await tool_registry.execute_tool(
                        name=fn_name,
                        arguments=parsed_args,
                        context=runtime_context,
                    )
                    if (
                        isinstance(result_output, str)
                        and result_output.startswith("Error:")
                    ):
                        is_error = True

                yield {
                    "type": "tool_call_result",
                    "id": call_id,
                    "name": fn_name,
                    "output": str(result_output),
                    "is_error": is_error,
                }

                # Persist tool execution result message in DB
                await add_message(
                    conversation_id=conversation_id,
                    role=MessageRole.TOOL,
                    content=str(result_output),
                    tool_call_id=call_id,
                    name=fn_name,
                )

        await update_conversation(conversation_id, status=ConversationStatus.ACTIVE)
        yield {"type": "done"}


agent_engine = AgentEngine()
