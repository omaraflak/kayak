"""The event vocabulary streamed to the UI.

Every event the server pushes over SSE is built here rather than as a dict literal at
the point of use. Two reasons: the type name says what the event is, so a reader does
not have to infer it from a string key; and the shape is declared once, so the browser
and the server cannot drift apart silently when a field is renamed.

The wire format is JSON, so these are TypedDicts rather than models: they are ordinary
dicts at runtime and cost nothing to serialize.
"""

from typing import List, Literal, Optional, TypedDict, Union

from backend.app.models import TaskStatus

# --------------------------------------------------------------- assistant output


class TokenEvent(TypedDict):
    """A fragment of the assistant's visible answer."""
    type: Literal["token"]
    content: str


class ThinkingEvent(TypedDict):
    """A fragment of the assistant's reasoning, shown collapsed."""
    type: Literal["thinking"]
    content: str


def token(content: str) -> TokenEvent:
    return {"type": "token", "content": content}


def thinking(content: str) -> ThinkingEvent:
    return {"type": "thinking", "content": content}


# ------------------------------------------------------------------- tool calls


class ToolCallDeltaEvent(TypedDict):
    """A tool call being assembled from the model's stream."""
    type: Literal["tool_call_delta"]
    index: int
    id: str
    name: str
    arguments: str


class ToolCallExecutingEvent(TypedDict):
    """A tool call that has started running."""
    type: Literal["tool_call_executing"]
    id: str
    name: str
    arguments: str


class ToolCallResultEvent(TypedDict):
    """The outcome of a tool call."""
    type: Literal["tool_call_result"]
    id: str
    name: str
    output: str
    is_error: bool


class ToolApprovalRequiredEvent(TypedDict):
    """A tool call waiting for the user to allow or refuse it."""
    type: Literal["tool_approval_required"]
    id: str
    name: str
    arguments: str


def tool_call_delta(
    index: int, call_id: str, name: str, arguments: str
) -> ToolCallDeltaEvent:
    return {
        "type": "tool_call_delta",
        "index": index,
        "id": call_id,
        "name": name,
        "arguments": arguments,
    }


def tool_call_executing(
    call_id: str, name: str, arguments: str
) -> ToolCallExecutingEvent:
    return {
        "type": "tool_call_executing",
        "id": call_id,
        "name": name,
        "arguments": arguments,
    }


def tool_call_result(
    call_id: str, name: str, output: str, is_error: bool
) -> ToolCallResultEvent:
    return {
        "type": "tool_call_result",
        "id": call_id,
        "name": name,
        "output": output,
        "is_error": is_error,
    }


def tool_approval_required(
    call_id: str, name: str, arguments: str
) -> ToolApprovalRequiredEvent:
    return {
        "type": "tool_approval_required",
        "id": call_id,
        "name": name,
        "arguments": arguments,
    }


# ---------------------------------------------------------------- turn lifecycle


class ErrorEvent(TypedDict):
    """The turn could not continue. Always terminal."""
    type: Literal["error"]
    error: str


class WarningEvent(TypedDict):
    """The turn continues, but degraded in a way worth saying out loud."""
    type: Literal["warning"]
    warning: str


class MaxIterationsEvent(TypedDict):
    """The turn stopped at its tool-step ceiling with work left over."""
    type: Literal["max_iterations"]
    limit: int
    content: str


class DoneEvent(TypedDict):
    """The turn finished. Sent however it ended, including after an error."""
    type: Literal["done"]


class CancelledEvent(TypedDict):
    """The turn was stopped by the user."""
    type: Literal["cancelled"]


def error(message: str) -> ErrorEvent:
    return {"type": "error", "error": message}


def warning(message: str) -> WarningEvent:
    return {"type": "warning", "warning": message}


def max_iterations(limit: int, content: str) -> MaxIterationsEvent:
    return {"type": "max_iterations", "limit": limit, "content": content}


def done() -> DoneEvent:
    return {"type": "done"}


def cancelled() -> CancelledEvent:
    return {"type": "cancelled"}


# ------------------------------------------------------------ conversation state


class UserMessageEvent(TypedDict):
    """A message the user sent, echoed to this conversation's other viewers."""
    type: Literal["user_message"]
    message: dict


class TitleUpdatedEvent(TypedDict):
    """The conversation was retitled."""
    type: Literal["title_updated"]
    title: str


class HistoryChangedEvent(TypedDict):
    """The transcript was rewritten; the client must reload it."""
    type: Literal["history_changed"]


class ConnectedEvent(TypedDict):
    """Opening handshake, carrying enough state to restore the composer."""
    type: Literal["connected"]
    status: str
    is_running: bool


class PingEvent(TypedDict):
    """Keeps an idle stream from being closed by an intermediary."""
    type: Literal["ping"]


def user_message(message: dict) -> UserMessageEvent:
    return {"type": "user_message", "message": message}


def title_updated(title: str) -> TitleUpdatedEvent:
    return {"type": "title_updated", "title": title}


def history_changed() -> HistoryChangedEvent:
    return {"type": "history_changed"}


def connected(status: str, is_running: bool) -> ConnectedEvent:
    return {"type": "connected", "status": status, "is_running": is_running}


def ping() -> PingEvent:
    return {"type": "ping"}


# --------------------------------------------------------------- background tasks


class TaskStartedEvent(TypedDict, total=False):
    """A background task began running."""
    type: Literal["task_started"]
    task_id: str
    name: str
    pid: int


class TaskOutputEvent(TypedDict):
    """A chunk of a running task's output."""
    type: Literal["task_output"]
    task_id: str
    stream: Literal["stdout", "stderr"]
    text: str


class TaskFinishedEvent(TypedDict, total=False):
    """A background task ended, however it ended."""
    type: Literal["task_finished"]
    task_id: str
    name: str
    status: str
    exit_code: Optional[int]
    error: str


class SubagentStartedEvent(TypedDict):
    """A delegated agent started work in its own conversation."""
    type: Literal["subagent_started"]
    task_id: str
    subagent_conversation_id: str
    agent_id: str


class SubagentFinishedEvent(TypedDict, total=False):
    """A delegated agent finished, with its answer or its failure."""
    type: Literal["subagent_finished"]
    task_id: str
    subagent_conversation_id: str
    status: str
    result: str
    error: str


def task_started(task_id: str, name: str, pid: Optional[int] = None) -> TaskStartedEvent:
    event: TaskStartedEvent = {"type": "task_started", "task_id": task_id, "name": name}
    if pid is not None:
        event["pid"] = pid
    return event


def task_output(task_id: str, text: str, is_stderr: bool) -> TaskOutputEvent:
    return {
        "type": "task_output",
        "task_id": task_id,
        "stream": "stderr" if is_stderr else "stdout",
        "text": text,
    }


def task_finished(
    task_id: str,
    name: str,
    status: TaskStatus,
    exit_code: Optional[int] = None,
    error: Optional[str] = None,
) -> TaskFinishedEvent:
    event: TaskFinishedEvent = {
        "type": "task_finished",
        "task_id": task_id,
        "name": name,
        "status": status.value,
        "exit_code": exit_code,
    }
    if error is not None:
        event["error"] = error
    return event


def subagent_started(
    task_id: str, subagent_conversation_id: str, agent_id: str
) -> SubagentStartedEvent:
    return {
        "type": "subagent_started",
        "task_id": task_id,
        "subagent_conversation_id": subagent_conversation_id,
        "agent_id": agent_id,
    }


def subagent_finished(
    task_id: str,
    subagent_conversation_id: str,
    status: str,
    result: Optional[str] = None,
    error: Optional[str] = None,
) -> SubagentFinishedEvent:
    event: SubagentFinishedEvent = {
        "type": "subagent_finished",
        "task_id": task_id,
        "subagent_conversation_id": subagent_conversation_id,
        "status": status,
    }
    if result is not None:
        event["result"] = result
    if error is not None:
        event["error"] = error
    return event


# ------------------------------------------------------------------- activity


class ConversationActivityEvent(TypedDict):
    """One conversation started or stopped working."""
    type: Literal["conversation_activity"]
    conversation_id: str
    running: bool


class ActivitySnapshotEvent(TypedDict):
    """Everything currently running, sent when a client connects."""
    type: Literal["snapshot"]
    running: List[str]


def conversation_activity(
    conversation_id: str, running: bool
) -> ConversationActivityEvent:
    return {
        "type": "conversation_activity",
        "conversation_id": conversation_id,
        "running": running,
    }


def activity_snapshot(running: List[str]) -> ActivitySnapshotEvent:
    return {"type": "snapshot", "running": running}


#: Anything the activity stream carries.
ActivityEvent = Union[ConversationActivityEvent, ActivitySnapshotEvent]

#: Anything the engine yields for one turn.
AgentEvent = Union[
    TokenEvent,
    ThinkingEvent,
    ToolCallDeltaEvent,
    ToolCallExecutingEvent,
    ToolCallResultEvent,
    ToolApprovalRequiredEvent,
    ErrorEvent,
    WarningEvent,
    MaxIterationsEvent,
    DoneEvent,
]

#: Anything a background task or sub-agent reports.
TaskEvent = Union[
    TaskStartedEvent,
    TaskOutputEvent,
    TaskFinishedEvent,
    SubagentStartedEvent,
    SubagentFinishedEvent,
]

#: Anything that can travel down a conversation's SSE stream.
ConversationEvent = Union[
    AgentEvent,
    TaskEvent,
    UserMessageEvent,
    TitleUpdatedEvent,
    HistoryChangedEvent,
    ConnectedEvent,
    PingEvent,
    CancelledEvent,
]
