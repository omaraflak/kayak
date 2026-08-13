import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Set, Tuple
import uuid
from backend.app.agent.approvals import approval_registry
from backend.app.agent.history import (
    MAX_HISTORY_CHARS,
    repair_tool_call_pairing,
    truncate_to_budget,
    truncate_tool_result,
)
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
    ToolPermission,
)
from backend.app.tools.permissions import format_denial, resolve_tool_permission
from backend.app.tools.registry import tool_registry

CANCELLED_RESULT = (
    "[Not executed: the user cancelled this turn before the tool ran.]"
)

REJECTED_RESULT = (
    "Error: The user declined to approve this tool call. Do not retry it; "
    "explain what you wanted to do and ask how to proceed."
)

APPROVAL_TIMEOUT_RESULT = (
    "Error: This tool call timed out waiting for user approval and was not executed."
)

MAX_ITERATIONS_NOTICE = (
    "I stopped because I reached the maximum number of tool-use steps allowed for a "
    "single turn. The work above is incomplete. Tell me to continue if you want me to "
    "keep going from here."
)


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
    depth: int,
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

    # The allowlist is exhaustive, so an agent with no permitted tools is offered
    # none rather than all of them.
    tool_schemas = tool_registry.get_tool_definitions(
        allowed_names=agent_config.allowed_tools
    )

    runtime_ctx: Dict[str, Any] = {
        "conversation_id": conversation_id,
        "workspace_dir": resolved_workspace,
        "container_id": effective_container_id,
        "task_manager": task_manager,
        "agent_depth": depth,
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
    """Constructs the full prompt message sequence for LiteLLM from database history.

    History is repaired before it is sent so that an interrupted turn cannot leave
    dangling tool calls, and trimmed to a character budget so a long run degrades by
    forgetting its oldest steps rather than by failing outright.
    """
    system_prompt_text = build_system_prompt(
        agent_config=session.agent_config,
        workspace_dir=session.workspace_dir,
        container_id=session.container_id,
    )

    llm_messages: List[Dict[str, Any]] = [
        {"role": MessageRole.SYSTEM.value, "content": system_prompt_text}
    ]

    for msg in repair_tool_call_pairing(raw_messages):
        role_str = msg.role.value if isinstance(msg.role, MessageRole) else str(msg.role)
        entry: Dict[str, Any] = {"role": role_str}
        if msg.content is not None:
            entry["content"] = (
                truncate_tool_result(msg.content)
                if msg.role == MessageRole.TOOL
                else msg.content
            )
        if msg.tool_calls:
            entry["tool_calls"] = msg.tool_calls
        if msg.tool_call_id:
            entry["tool_call_id"] = msg.tool_call_id
        if msg.name:
            entry["name"] = msg.name
        llm_messages.append(entry)

    return truncate_to_budget(llm_messages, max_chars=MAX_HISTORY_CHARS)


def _update_tool_delta(
    active_tool_calls: Dict[int, Dict[str, Any]],
    chunk: Dict[str, Any],
    iteration: int,
) -> Dict[str, Any]:
    """Accumulates streaming tool call delta fragments into active_tool_calls state."""
    raw_index = chunk.get("index")
    idx = int(raw_index) if raw_index is not None else 0
    if idx not in active_tool_calls:
        active_tool_calls[idx] = {
            # Providers that omit ids still need one that is unique for the whole
            # conversation: an index/iteration pair repeats on every turn and would
            # collide with calls from earlier turns.
            "id": chunk.get("id") or f"call_{iteration}_{idx}_{uuid.uuid4().hex[:12]}",
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

    if not isinstance(parsed_args, dict):
        return "Error: Tool arguments must be a JSON object.", True

    result_output = await tool_registry.execute_tool(
        name=fn_name,
        arguments=parsed_args,
        context=session.runtime_context,
    )
    is_error = isinstance(result_output, str) and result_output.startswith("Error:")
    return str(result_output), is_error


async def _record_tool_result(
    conversation_id: str,
    call_id: str,
    tool_name: str,
    output: str,
) -> None:
    """Persists a tool result, shielded so it still lands during cancellation."""
    await asyncio.shield(
        add_message(
            conversation_id=conversation_id,
            role=MessageRole.TOOL,
            content=output,
            tool_call_id=call_id,
            name=tool_name,
        )
    )


async def _close_unexecuted_tool_calls(
    conversation_id: str,
    tool_calls: List[Dict[str, Any]],
    executed_ids: Set[str],
    reason: str,
) -> None:
    """Writes placeholder results for tool calls that never ran.

    Without this an interrupted turn leaves the assistant message requesting calls
    that nothing ever answers, which every provider rejects on the next turn.
    """
    for tc in tool_calls:
        if tc["id"] in executed_ids:
            continue
        try:
            await _record_tool_result(
                conversation_id=conversation_id,
                call_id=tc["id"],
                tool_name=tc["function"]["name"],
                output=reason,
            )
        except Exception:
            # History repair covers anything that fails to persist here.
            pass


class AgentEngine:
    """Core autonomous agent execution loop managing prompt injection, tool dispatching, and streaming."""

    async def run(
        self,
        conversation_id: str,
        agent_id: Optional[str] = None,
        workspace_dir: Optional[Path] = None,
        container_id: Optional[str] = None,
        max_iterations: Optional[int] = None,
        depth: int = 0,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Runs the ReAct agent turn loop for a conversation, streaming step events and persisting messages.

        Args:
            conversation_id: ID of the conversation.
            agent_id: Optional agent profile override.
            workspace_dir: Optional custom workspace path.
            container_id: Optional Docker container identifier.
            max_iterations: Safety ceiling on tool execution loop turns.
            depth: Sub-agent nesting depth of this run.

        Yields:
            Dict[str, Any]: SSE-compatible event dictionaries (tokens, tool executions, errors, completion).
        """
        session, error = await _resolve_session_context(
            conversation_id=conversation_id,
            agent_id=agent_id,
            workspace_dir=workspace_dir,
            container_id=container_id,
            depth=depth,
        )
        if error or not session:
            yield {"type": "error", "error": error}
            return

        iteration_ceiling = max_iterations or settings.AGENT_MAX_ITERATIONS
        await update_conversation(conversation_id, status=ConversationStatus.RUNNING)
        hit_ceiling = True

        try:
            for iteration in range(iteration_ceiling):
                raw_messages = await get_messages(conversation_id)
                llm_messages = _build_llm_messages(raw_messages, session)

                assistant_content = ""
                assistant_thinking = ""
                active_tool_calls: Dict[int, Dict[str, Any]] = {}
                stream_failed = False

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

                    elif chunk_type == "warning":
                        yield {"type": "warning", "warning": chunk.get("warning")}

                    elif chunk_type == "error":
                        yield {"type": "error", "error": chunk.get("error")}
                        stream_failed = True
                        break

                if stream_failed:
                    # Persist whatever was streamed before the failure so the partial
                    # turn is not silently lost, but drop any half-built tool calls.
                    if assistant_content or assistant_thinking:
                        await add_message(
                            conversation_id=conversation_id,
                            role=MessageRole.ASSISTANT,
                            content=assistant_content or None,
                            thinking=assistant_thinking.strip() or None,
                        )
                    hit_ceiling = False
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
                    hit_ceiling = False
                    break

                executed_ids: Set[str] = set()
                try:
                    async for event in self._run_tool_calls(
                        final_tool_calls, session, executed_ids
                    ):
                        yield event
                except asyncio.CancelledError:
                    await _close_unexecuted_tool_calls(
                        conversation_id, final_tool_calls, executed_ids, CANCELLED_RESULT
                    )
                    raise

            if hit_ceiling:
                await add_message(
                    conversation_id=conversation_id,
                    role=MessageRole.ASSISTANT,
                    content=MAX_ITERATIONS_NOTICE,
                )
                yield {
                    "type": "max_iterations",
                    "limit": iteration_ceiling,
                    "content": MAX_ITERATIONS_NOTICE,
                }
        finally:
            approval_registry.cancel_conversation(conversation_id)
            await asyncio.shield(
                update_conversation(conversation_id, status=ConversationStatus.ACTIVE)
            )

        yield {"type": "done"}

    async def _run_tool_calls(
        self,
        tool_calls: List[Dict[str, Any]],
        session: _SessionContext,
        executed_ids: Set[str],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Executes each requested tool call, enforcing agent permissions along the way."""
        for tc in tool_calls:
            call_id = tc["id"]
            fn_name = tc["function"]["name"]
            raw_args = tc["function"]["arguments"]

            permission = resolve_tool_permission(session.agent_config, fn_name)

            if permission == ToolPermission.DENIED:
                output = format_denial(fn_name, session.agent_config)
                yield {
                    "type": "tool_call_result",
                    "id": call_id,
                    "name": fn_name,
                    "output": output,
                    "is_error": True,
                }
                await _record_tool_result(
                    session.conversation_id, call_id, fn_name, output
                )
                executed_ids.add(call_id)
                continue

            if permission == ToolPermission.ASK_USER:
                # Register before announcing, so a decision arriving immediately
                # after the event has something to resolve.
                pending = approval_registry.register(
                    call_id=call_id,
                    conversation_id=session.conversation_id,
                    tool_name=fn_name,
                    arguments=raw_args,
                )
                yield {
                    "type": "tool_approval_required",
                    "id": call_id,
                    "name": fn_name,
                    "arguments": raw_args,
                }
                approved = await approval_registry.wait(call_id, pending)
                if approved is not True:
                    output = (
                        APPROVAL_TIMEOUT_RESULT if approved is None else REJECTED_RESULT
                    )
                    yield {
                        "type": "tool_call_result",
                        "id": call_id,
                        "name": fn_name,
                        "output": output,
                        "is_error": True,
                    }
                    await _record_tool_result(
                        session.conversation_id, call_id, fn_name, output
                    )
                    executed_ids.add(call_id)
                    continue

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

            await _record_tool_result(
                session.conversation_id, call_id, fn_name, output
            )
            executed_ids.add(call_id)


agent_engine = AgentEngine()
