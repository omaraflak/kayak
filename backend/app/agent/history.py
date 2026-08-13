"""Conversation history normalization and context-window budgeting.

Providers reject a message list in which an assistant turn requests tool calls that
are never answered, or in which a tool result refers to a call that was never made.
Both shapes are produced by ordinary events -- a cancelled turn, an iteration ceiling,
a server restart mid-turn -- so history is repaired on the way to the model rather
than trusted to be well-formed.
"""

import json
from typing import Any, Dict, List

from backend.app.models import Message, MessageRole

# Per-tool-result cap. Command output and file reads are unbounded at the source,
# and a single runaway result can otherwise consume the entire context window.
MAX_TOOL_RESULT_CHARS = 20_000

# Total budget for the serialized message list, excluding the system prompt.
# Roughly 100k tokens, low enough to leave headroom on small local models.
MAX_HISTORY_CHARS = 400_000

INTERRUPTED_RESULT = (
    "[No result recorded: this tool call was interrupted before it finished. "
    "Assume it did not run and retry it if the result is still needed.]"
)

TRUNCATION_NOTICE = (
    "[Earlier messages in this conversation were omitted to stay within the model's "
    "context window. Re-read any files or re-run any commands whose results you need.]"
)


def truncate_tool_result(text: str, max_chars: int = MAX_TOOL_RESULT_CHARS) -> str:
    """Caps an oversized tool result, keeping the head and tail where signal usually is."""
    if len(text) <= max_chars:
        return text

    head_chars = int(max_chars * 0.6)
    tail_chars = max_chars - head_chars
    omitted = len(text) - max_chars
    return (
        f"{text[:head_chars]}\n\n"
        f"... [{omitted} characters omitted from the middle of this output] ...\n\n"
        f"{text[-tail_chars:]}"
    )


def repair_tool_call_pairing(messages: List[Message]) -> List[Message]:
    """Returns a message list in which every tool call and tool result is paired.

    Assistant tool calls with no recorded result get a synthetic result explaining the
    interruption. Tool results referring to a call that was never requested, and
    duplicate results for the same call, are dropped.

    Args:
        messages: Chronological conversation history straight from the database.

    Returns:
        List[Message]: History that is safe to send to a provider.
    """
    answered: Dict[str, Message] = {}
    for msg in messages:
        if msg.role == MessageRole.TOOL and msg.tool_call_id:
            answered.setdefault(msg.tool_call_id, msg)

    repaired: List[Message] = []
    requested: set[str] = set()
    emitted: set[str] = set()

    for msg in messages:
        if msg.role == MessageRole.TOOL:
            call_id = msg.tool_call_id
            # Drop results that answer nothing, and any duplicate for the same call.
            if not call_id or call_id not in requested or call_id in emitted:
                continue
            emitted.add(call_id)
            repaired.append(msg)
            continue

        repaired.append(msg)

        if msg.role != MessageRole.ASSISTANT or not msg.tool_calls:
            continue

        for tool_call in msg.tool_calls:
            call_id = tool_call.get("id")
            if not call_id or call_id in requested:
                continue
            requested.add(call_id)
            if call_id in answered:
                continue
            # No result was ever persisted for this call: synthesize one so the
            # assistant turn is well-formed.
            emitted.add(call_id)
            repaired.append(
                Message(
                    conversation_id=msg.conversation_id,
                    role=MessageRole.TOOL,
                    content=INTERRUPTED_RESULT,
                    tool_call_id=call_id,
                    name=tool_call.get("function", {}).get("name"),
                    created_at=msg.created_at,
                )
            )

    return repaired


def _entry_size(entry: Dict[str, Any]) -> int:
    """Approximates the serialized size of a single provider message."""
    try:
        return len(json.dumps(entry, default=str))
    except Exception:
        return len(str(entry))


def group_into_atomic_blocks(
    entries: List[Dict[str, Any]],
) -> List[List[Dict[str, Any]]]:
    """Groups provider messages so an assistant turn stays attached to its tool results.

    Dropping an assistant message while keeping its tool results (or the reverse)
    recreates exactly the malformed history this module exists to prevent, so
    truncation operates on whole blocks.
    """
    blocks: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []

    for entry in entries:
        if entry.get("role") == MessageRole.TOOL.value and current:
            current.append(entry)
            continue
        if current:
            blocks.append(current)
        current = [entry]

    if current:
        blocks.append(current)
    return blocks


def truncate_to_budget(
    entries: List[Dict[str, Any]],
    max_chars: int = MAX_HISTORY_CHARS,
) -> List[Dict[str, Any]]:
    """Drops the oldest history until the message list fits the character budget.

    The leading system prompt is always preserved. The most recent block is always
    preserved even if it alone exceeds the budget, since dropping it would leave the
    model with nothing to answer.

    Args:
        entries: Provider-formatted messages, system prompt first.
        max_chars: Budget for everything after the system prompt.

    Returns:
        List[Dict[str, Any]]: Messages within budget, with a notice where content was cut.
    """
    if not entries:
        return entries

    system_entries: List[Dict[str, Any]] = []
    body = entries
    if entries[0].get("role") == MessageRole.SYSTEM.value:
        system_entries = [entries[0]]
        body = entries[1:]

    blocks = group_into_atomic_blocks(body)
    if not blocks:
        return system_entries

    kept: List[List[Dict[str, Any]]] = []
    used = 0
    for block in reversed(blocks):
        block_size = sum(_entry_size(entry) for entry in block)
        if kept and used + block_size > max_chars:
            break
        kept.append(block)
        used += block_size

    kept.reverse()
    if len(kept) == len(blocks):
        return entries

    notice = {"role": MessageRole.USER.value, "content": TRUNCATION_NOTICE}
    return system_entries + [notice] + [entry for block in kept for entry in block]
