"""Global stream of which conversations are currently working.

Deliberately not nested under `/api/conversations`: this is one connection for
the whole app, not a per-conversation one, and keeping it on its own prefix
also avoids being shadowed by the `/{conversation_id}` route.

Every stream opens with a snapshot, so a client that connects late -- or
reconnects after the server restarts -- is immediately correct without needing
to have witnessed the events it missed.
"""

import asyncio
import json
from typing import Any, AsyncGenerator, Dict
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from backend.app.agent.activity import activity_tracker

router = APIRouter(prefix="/api/activity", tags=["activity"])

# Idle streams send a comment-like ping so proxies do not time the connection out.
_PING_INTERVAL_SECONDS = 20.0


@router.get("/events")
async def stream_activity(request: Request) -> StreamingResponse:
    """Streams conversation activity changes to a single client."""
    queue = activity_tracker.subscribe()

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            snapshot: Dict[str, Any] = {
                "type": "snapshot",
                "running": activity_tracker.running_ids(),
            }
            yield f"data: {json.dumps(snapshot)}\n\n"

            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=_PING_INTERVAL_SECONDS
                    )
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type': 'ping'})}\n\n"
        finally:
            activity_tracker.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
