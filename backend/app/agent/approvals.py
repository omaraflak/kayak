"""Human-in-the-loop approval gate for tool calls marked ``ask_user``.

The agent turn runs as a background task streaming events to the browser, so an
approval is a request/response pair across that boundary: the engine parks on an
event keyed by tool call id, and the REST endpoint the UI hits resolves it.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# An unanswered prompt should not pin an agent turn forever; a closed browser tab
# would otherwise leak the task and leave the conversation stuck as RUNNING.
DEFAULT_APPROVAL_TIMEOUT_SECONDS = 300


@dataclass
class _PendingApproval:
    """A tool call awaiting a decision from the user."""
    conversation_id: str
    tool_name: str
    #: The call's raw arguments, kept so a reconnecting client can be shown the
    #: same prompt it would have received live.
    arguments: str = ""
    event: asyncio.Event = field(default_factory=asyncio.Event)
    approved: Optional[bool] = None


class ApprovalRegistry:
    """Tracks tool calls parked awaiting user approval."""

    def __init__(self) -> None:
        self._pending: Dict[str, _PendingApproval] = {}

    def register(
        self,
        call_id: str,
        conversation_id: str,
        tool_name: str,
        arguments: str = "",
    ) -> _PendingApproval:
        """Records a tool call as awaiting approval and returns its handle.

        Registration is deliberately separate from waiting. The caller must register
        *before* announcing the request to the client: a decision that arrives
        between the announcement and the wait would otherwise find nothing to
        resolve and be dropped, stalling the turn until its timeout.
        """
        pending = _PendingApproval(
            conversation_id=conversation_id,
            tool_name=tool_name,
            arguments=arguments,
        )
        self._pending[call_id] = pending
        return pending

    def list_for_conversation(self, conversation_id: str) -> List[Dict[str, Any]]:
        """Describes the tool calls a conversation is currently parked on.

        A reconnecting client has no other way to learn that the turn is waiting on
        it: the approval request was announced once, over a stream it has since
        dropped. Without this the conversation appears idle while the engine waits
        out its full timeout.
        """
        return [
            {
                "id": call_id,
                "name": pending.tool_name,
                "arguments": pending.arguments,
            }
            for call_id, pending in self._pending.items()
            if pending.conversation_id == conversation_id
        ]

    async def wait(
        self,
        call_id: str,
        pending: _PendingApproval,
        timeout_seconds: int = DEFAULT_APPROVAL_TIMEOUT_SECONDS,
    ) -> Optional[bool]:
        """Blocks until the user decides on a registered tool call, or it times out.

        Args:
            call_id: Identifier of the tool call being gated.
            pending: Handle returned by :meth:`register`.
            timeout_seconds: How long to wait before giving up.

        Returns:
            Optional[bool]: True if approved, False if rejected, None on timeout.
        """
        try:
            await asyncio.wait_for(pending.event.wait(), timeout=timeout_seconds)
            return pending.approved
        except asyncio.TimeoutError:
            return None
        finally:
            self._pending.pop(call_id, None)

    def resolve(self, call_id: str, approved: bool) -> bool:
        """Records a user decision and wakes the parked agent turn.

        Args:
            call_id: Identifier of the tool call being decided.
            approved: Whether the user allowed the call.

        Returns:
            bool: True if a pending call was resolved, False if there was none.
        """
        pending = self._pending.get(call_id)
        if not pending:
            return False
        pending.approved = approved
        pending.event.set()
        return True

    def cancel_conversation(self, conversation_id: str) -> None:
        """Rejects every pending approval for a conversation.

        Called when a turn is cancelled or deleted so that a parked engine unblocks
        instead of waiting out its full timeout.
        """
        for call_id, pending in list(self._pending.items()):
            if pending.conversation_id == conversation_id:
                pending.approved = False
                pending.event.set()
                # Drop it here too: a turn abandoned between register() and wait()
                # would otherwise leave the entry behind forever.
                self._pending.pop(call_id, None)


approval_registry = ApprovalRegistry()
