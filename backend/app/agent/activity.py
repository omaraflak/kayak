"""Live record of which conversations are currently running a turn.

The sidebar shows this on every page, but the information used to travel up
from the open conversation's chat pane -- so leaving the chat tab unmounted the
pane and silently cleared the indicator while the agent was still working. The
stored conversation status cannot stand in for it either: a client that is not
looking at a conversation holds no stream, so nothing tells it when that
conversation starts or stops.

The truth therefore lives here, in the process running the turns, and is
broadcast to every listener regardless of what any client happens to be
looking at. Sub-agent runs are included, because the engine reports for them
too.
"""

import asyncio
import logging
from typing import Any, Dict, List, Set

logger = logging.getLogger(__name__)

# A listener that stops draining must not grow its queue without bound. Status
# events are tiny and rare, so this ceiling is only ever reached by a client
# that has effectively gone away; it is then dropped and re-syncs on reconnect
# from the snapshot every new stream begins with.
_QUEUE_MAXSIZE = 100


class ActivityTracker:
    """Tracks running conversations and notifies subscribers when that changes."""

    def __init__(self) -> None:
        self._running: Set[str] = set()
        self._listeners: List[asyncio.Queue[Dict[str, Any]]] = []

    def running_ids(self) -> List[str]:
        """Conversations with a turn in flight right now."""
        return sorted(self._running)

    def subscribe(self) -> asyncio.Queue[Dict[str, Any]]:
        """Registers a listener queue for subsequent status changes."""
        queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._listeners.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Dict[str, Any]]) -> None:
        """Removes a listener queue."""
        if queue in self._listeners:
            self._listeners.remove(queue)

    def set_running(self, conversation_id: str) -> None:
        """Marks a conversation as working, announcing the change once."""
        if conversation_id in self._running:
            return
        self._running.add(conversation_id)
        self._broadcast(
            {"type": "conversation_activity", "conversation_id": conversation_id, "running": True}
        )

    def set_idle(self, conversation_id: str) -> None:
        """Marks a conversation as finished, announcing the change once."""
        if conversation_id not in self._running:
            return
        self._running.discard(conversation_id)
        self._broadcast(
            {"type": "conversation_activity", "conversation_id": conversation_id, "running": False}
        )

    def reset(self) -> None:
        """Clears all state. Used when a process takes over after a restart."""
        self._running.clear()

    def _broadcast(self, event: Dict[str, Any]) -> None:
        for queue in list(self._listeners):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("Dropping activity listener that is not draining events.")
                self.unsubscribe(queue)


activity_tracker = ActivityTracker()
