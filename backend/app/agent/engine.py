import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Set, Tuple, Union
import uuid
from backend.app.agent import events
from backend.app.agent.activity import activity_tracker
from backend.app.agent.approvals import approval_registry
from backend.app.agent.events import AgentEvent
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
        # Which profile is running this turn, so tools like spawn_subagent can
        # enforce per-agent policy rather than trusting the model's own claims.
        "caller_agent_id": agent_config.id,
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
) -> events.ToolCallDeltaEvent:
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

    call = active_tool_calls[idx]
    return events.tool_call_delta(idx, call["id"], call["name"], call["arguments"])


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


def _is_parallelizable(tc: Dict[str, Any], session: _SessionContext) -> bool:
    """Whether a call can safely run alongside others.

    A tool qualifies only if it declares itself read-only and the agent auto-approves
    it: anything that mutates state could race with its neighbours, and anything the
    user has to approve has to be asked about one call at a time.
    """
    fn_name = tc["function"]["name"]
    return (
        resolve_tool_permission(session.agent_config, fn_name)
        == ToolPermission.AUTO_APPROVE
        and tool_registry.is_read_only(fn_name)
    )


def _group_tool_calls(
    tool_calls: List[Dict[str, Any]], session: _SessionContext
) -> List[List[Dict[str, Any]]]:
    """Splits one turn's calls into batches that may run together.

    Adjacent parallelizable calls share a batch; every other call gets a batch of its
    own. Only adjacent ones are merged, so `read, read, write, read` still runs the
    write after the first two reads and the last read after the write.
    """
    groups: List[List[Dict[str, Any]]] = []
    previous_was_parallelizable = False
    for tc in tool_calls:
        parallelizable = _is_parallelizable(tc, session)
        if parallelizable and previous_was_parallelizable:
            groups[-1].append(tc)
        else:
            groups.append([tc])
        previous_was_parallelizable = parallelizable
    return groups


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


@dataclass
class _AssistantTurn:
    """What one pass of the model produced."""
    content: str
    thinking: str
    tool_calls: List[Dict[str, Any]]
    failed: bool


class AgentEngine:
    """Core autonomous agent execution loop managing prompt injection, tool dispatching, and streaming."""

    async def _stream_assistant_turn(
        self, session: _SessionContext, iteration: int
    ) -> AsyncGenerator[Union[AgentEvent, _AssistantTurn], None]:
        """Streams one model response, yielding its events and finally the result.

        The last item yielded is the `_AssistantTurn` describing what was produced;
        everything before it is an event for the client.
        """
        raw_messages = await get_messages(session.conversation_id)
        llm_messages = _build_llm_messages(raw_messages, session)

        content = ""
        reasoning = ""
        active_tool_calls: Dict[int, Dict[str, Any]] = {}
        failed = False

        async for chunk in generate_completion_stream(
            model=session.agent_config.model,
            messages=llm_messages,
            tools=session.tool_schemas,
            temperature=session.agent_config.temperature,
        ):
            chunk_type = chunk.get("type")

            if chunk_type == "thinking":
                text = chunk.get("content", "")
                reasoning += text
                yield events.thinking(text)

            elif chunk_type == "token":
                text = chunk.get("content", "")
                content += text
                yield events.token(text)

            elif chunk_type == "tool_call_delta":
                yield _update_tool_delta(active_tool_calls, chunk, iteration)

            elif chunk_type == "warning":
                yield events.warning(str(chunk.get("warning", "")))

            elif chunk_type == "error":
                yield events.error(str(chunk.get("error", "")))
                failed = True
                break

        yield _AssistantTurn(
            content=content,
            thinking=reasoning,
            tool_calls=_finalize_tool_calls(active_tool_calls),
            failed=failed,
        )

    async def run(
        self,
        conversation_id: str,
        agent_id: Optional[str] = None,
        workspace_dir: Optional[Path] = None,
        container_id: Optional[str] = None,
        max_iterations: Optional[int] = None,
        depth: int = 0,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Runs the ReAct agent turn loop for a conversation, streaming step events and persisting messages.

        Args:
            conversation_id: ID of the conversation.
            agent_id: Optional agent profile override.
            workspace_dir: Optional custom workspace path.
            container_id: Optional Docker container identifier.
            max_iterations: Safety ceiling on tool execution loop turns.
            depth: Sub-agent nesting depth of this run.

        Yields:
            AgentEvent: One event per step (tokens, tool executions, errors, completion).
        """
        session, error = await _resolve_session_context(
            conversation_id=conversation_id,
            agent_id=agent_id,
            workspace_dir=workspace_dir,
            container_id=container_id,
            depth=depth,
        )
        if error or not session:
            yield events.error(error or "The conversation could not be started.")
            yield events.done()
            return

        iteration_ceiling = max_iterations or settings.AGENT_MAX_ITERATIONS
        await update_conversation(conversation_id, status=ConversationStatus.RUNNING)
        # Announced from here rather than from the route so that sub-agent runs,
        # which never pass through it, are reported too.
        activity_tracker.set_running(conversation_id)
        hit_ceiling = True

        try:
            for iteration in range(iteration_ceiling):
                turn: Optional[_AssistantTurn] = None
                async for item in self._stream_assistant_turn(session, iteration):
                    if isinstance(item, _AssistantTurn):
                        turn = item
                    else:
                        yield item
                assert turn is not None  # the generator always ends with the result

                if turn.failed:
                    # Persist whatever was streamed before the failure so the partial
                    # turn is not silently lost, but drop any half-built tool calls.
                    await self._persist_assistant_turn(conversation_id, turn, with_calls=False)
                    hit_ceiling = False
                    break

                await self._persist_assistant_turn(conversation_id, turn, with_calls=True)

                # Turn complete if no tool calls requested
                if not turn.tool_calls:
                    hit_ceiling = False
                    break

                executed_ids: Set[str] = set()
                try:
                    async for event in self._run_tool_calls(
                        turn.tool_calls, session, executed_ids
                    ):
                        yield event
                except asyncio.CancelledError:
                    await _close_unexecuted_tool_calls(
                        conversation_id, turn.tool_calls, executed_ids, CANCELLED_RESULT
                    )
                    raise

            if hit_ceiling:
                await add_message(
                    conversation_id=conversation_id,
                    role=MessageRole.ASSISTANT,
                    content=MAX_ITERATIONS_NOTICE,
                )
                yield events.max_iterations(iteration_ceiling, MAX_ITERATIONS_NOTICE)
        finally:
            approval_registry.cancel_conversation(conversation_id)
            activity_tracker.set_idle(conversation_id)
            await asyncio.shield(
                update_conversation(conversation_id, status=ConversationStatus.ACTIVE)
            )

        # Sent however the turn ended, including after an error: it is what releases
        # the composer, and a turn that failed used to leave the client waiting on an
        # event that never came.
        yield events.done()

    @staticmethod
    async def _persist_assistant_turn(
        conversation_id: str, turn: _AssistantTurn, with_calls: bool
    ) -> None:
        """Stores what the model produced, if it produced anything worth storing."""
        tool_calls = turn.tool_calls if with_calls else []
        if not turn.content and not turn.thinking.strip() and not tool_calls:
            return
        await add_message(
            conversation_id=conversation_id,
            role=MessageRole.ASSISTANT,
            content=turn.content or None,
            thinking=turn.thinking.strip() or None,
            tool_calls=tool_calls or None,
        )

    async def _run_parallel_group(
        self,
        group: List[Dict[str, Any]],
        session: _SessionContext,
        executed_ids: Set[str],
    ) -> AsyncGenerator[AgentEvent, None]:
        """Runs a batch of read-only, auto-approved calls at the same time.

        Every call in the batch is announced before any of them runs, so the client
        shows them working side by side, and results are emitted in the order the
        model asked for them rather than the order they happened to finish.
        """
        for tc in group:
            yield events.tool_call_executing(
                tc["id"], tc["function"]["name"], tc["function"]["arguments"]
            )

        limit = asyncio.Semaphore(settings.AGENT_MAX_CONCURRENT_TOOLS)

        async def _run_single(tool_call: Dict[str, Any]) -> Tuple[str, bool]:
            async with limit:
                try:
                    return await _execute_single_tool(tool_call, session)
                except Exception as e:
                    # One tool blowing up must not discard the results of the
                    # siblings it was gathered with.
                    name = tool_call["function"]["name"]
                    return f"Error executing tool '{name}': {str(e)}", True

        results = await asyncio.gather(*[_run_single(tc) for tc in group])

        for tc, (output, is_error) in zip(group, results):
            call_id = tc["id"]
            fn_name = tc["function"]["name"]
            yield events.tool_call_result(call_id, fn_name, output, is_error=is_error)
            await _record_tool_result(
                session.conversation_id, call_id, fn_name, output
            )
            executed_ids.add(call_id)

    async def _run_tool_calls(
        self,
        tool_calls: List[Dict[str, Any]],
        session: _SessionContext,
        executed_ids: Set[str],
    ) -> AsyncGenerator[AgentEvent, None]:
        """Executes each requested tool call, enforcing agent permissions along the way.

        Consecutive calls that are read-only and auto-approved run together; anything
        that can change state, or that needs the user to approve it first, runs on its
        own and in the order the model asked for it. Grouping only adjacent calls is
        what keeps a read that the model put after a write from overtaking it.
        """
        for group in _group_tool_calls(tool_calls, session):
            if len(group) > 1:
                async for event in self._run_parallel_group(
                    group, session, executed_ids
                ):
                    yield event
                continue

            tc = group[0]
            call_id = tc["id"]
            fn_name = tc["function"]["name"]
            raw_args = tc["function"]["arguments"]

            permission = resolve_tool_permission(session.agent_config, fn_name)

            if permission == ToolPermission.DENIED:
                output = format_denial(fn_name, session.agent_config)
                yield events.tool_call_result(call_id, fn_name, output, is_error=True)
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
                yield events.tool_approval_required(call_id, fn_name, raw_args)
                approved = await approval_registry.wait(call_id, pending)
                if approved is not True:
                    output = (
                        APPROVAL_TIMEOUT_RESULT if approved is None else REJECTED_RESULT
                    )
                    yield events.tool_call_result(call_id, fn_name, output, is_error=True)
                    await _record_tool_result(
                        session.conversation_id, call_id, fn_name, output
                    )
                    executed_ids.add(call_id)
                    continue

            yield events.tool_call_executing(call_id, fn_name, raw_args)

            output, is_error = await _execute_single_tool(tc, session)

            yield events.tool_call_result(call_id, fn_name, output, is_error=is_error)

            await _record_tool_result(
                session.conversation_id, call_id, fn_name, output
            )
            executed_ids.add(call_id)


agent_engine = AgentEngine()
