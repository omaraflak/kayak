"""Global stream of which conversations are currently working.

Deliberately not nested under `/api/conversations`: this is one connection for
the whole app, not a per-conversation one, and keeping it on its own prefix
also avoids being shadowed by the `/{conversation_id}` route.

Every stream opens with a snapshot, so a client that connects late -- or
reconnects after the server restarts -- is immediately correct without needing
to have witnessed the events it missed.
"""

from typing import AsyncIterator
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from backend.app import sse
from backend.app.agent import events
from backend.app.agent.activity import activity_tracker

router = APIRouter(prefix="/api/activity", tags=["activity"])


@router.get("/events")
async def stream_activity(request: Request) -> StreamingResponse:
    """Streams conversation activity changes to a single client."""
    queue = activity_tracker.subscribe()

    async def frames() -> AsyncIterator[str]:
        try:
            yield sse.frame(events.activity_snapshot(activity_tracker.running_ids()))
            async for chunk in sse.drain(request, queue, events.ping()):
                yield chunk
        finally:
            activity_tracker.unsubscribe(queue)

    return sse.response(frames())
