"""The live event hub for a conversation.

One place owns "who is listening to this conversation, and what have they missed":
the SSE endpoint subscribes, the turn runner publishes, and a client that reconnects
mid-turn is caught up from the replay buffer rather than staring at a turn that looks
idle while a command runs.
"""

import asyncio
import logging
from typing import Dict, Iterator, List, Optional

from backend.app.agent.events import ConversationEvent

logger = logging.getLogger(__name__)

#: A slow or backgrounded browser tab must not grow its queue without bound. When the
#: queue overflows the client is dropped; it reconnects and reloads from the database.
EVENT_QUEUE_MAXSIZE = 1000


class TurnReplay:
    """What a turn has produced so far, for a client that connects part-way through.

    Text alone was not enough: a turn spends most of its wall-clock time inside tools,
    so replaying only the prose left a reconnecting client watching a turn that
    appeared to be doing nothing.
    """

    def __init__(self) -> None:
        self.thinking = ""
        self.tokens = ""
        self.tool_calls: Dict[str, Dict[str, object]] = {}

    def record(self, event: ConversationEvent) -> None:
        """Folds one event into the replayable state of the turn."""
        event_type = event.get("type")

        if event_type == "thinking":
            self.thinking += str(event.get("content", ""))
        elif event_type == "token":
            self.tokens += str(event.get("content", ""))
        elif event_type in ("tool_call_executing", "tool_call_result"):
            call_id = event.get("id")
            if not isinstance(call_id, str):
                return
            entry = self.tool_calls.setdefault(call_id, {})
            entry.update({key: value for key, value in event.items() if key != "type"})
            entry["done"] = event_type == "tool_call_result"

    def events(self) -> Iterator[ConversationEvent]:
        """Replays the turn so far, in an order the client can apply directly."""
        if self.thinking:
            yield {"type": "thinking", "content": self.thinking}  # type: ignore[misc]
        if self.tokens:
            yield {"type": "token", "content": self.tokens}  # type: ignore[misc]

        for call_id, call in self.tool_calls.items():
            done = bool(call.get("done"))
            replay: Dict[str, object] = {
                "type": "tool_call_result" if done else "tool_call_executing",
                "id": call_id,
                "name": call.get("name", ""),
                "arguments": call.get("arguments", ""),
            }
            if done:
                replay["output"] = call.get("output", "")
                replay["is_error"] = bool(call.get("is_error"))
            yield replay  # type: ignore[misc]


class ConversationEventHub:
    """Fan-out of live events to everyone watching a conversation."""

    def __init__(self) -> None:
        self._queues: Dict[str, List[asyncio.Queue[ConversationEvent]]] = {}
        self._replays: Dict[str, TurnReplay] = {}

    # ------------------------------------------------------------- subscription

    def subscribe(self, conversation_id: str) -> asyncio.Queue[ConversationEvent]:
        """Registers a listener and returns the queue its events arrive on."""
        queue: asyncio.Queue[ConversationEvent] = asyncio.Queue(
            maxsize=EVENT_QUEUE_MAXSIZE
        )
        self._queues.setdefault(conversation_id, []).append(queue)
        return queue

    def unsubscribe(
        self, conversation_id: str, queue: asyncio.Queue[ConversationEvent]
    ) -> None:
        """Removes a listener, forgetting the conversation once nobody is left."""
        listeners = self._queues.get(conversation_id)
        if not listeners:
            return
        if queue in listeners:
            listeners.remove(queue)
        if not listeners:
            self._queues.pop(conversation_id, None)

    def publish(self, conversation_id: str, event: ConversationEvent) -> None:
        """Sends an event to every listener, recording it for late arrivals."""
        replay = self._replays.get(conversation_id)
        if replay is not None:
            replay.record(event)

        for queue in list(self._queues.get(conversation_id, [])):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                self.unsubscribe(conversation_id, queue)
                logger.warning(
                    "Dropping SSE listener for conversation %s: client is not draining"
                    " events.",
                    conversation_id,
                )

    # ------------------------------------------------------------------- replay

    def begin_turn(self, conversation_id: str) -> None:
        """Starts collecting a replay for a turn that is about to run."""
        self._replays[conversation_id] = TurnReplay()

    def end_turn(self, conversation_id: str) -> None:
        """Discards the replay once the turn is over and history is authoritative."""
        self._replays.pop(conversation_id, None)

    def replay_for(self, conversation_id: str) -> Optional[TurnReplay]:
        """The turn in flight for this conversation, if there is one."""
        return self._replays.get(conversation_id)


conversation_events = ConversationEventHub()
