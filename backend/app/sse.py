"""Server-sent event streams.

Every live view in the UI -- a conversation, the sidebar's activity, the local model
servers -- is one SSE stream, framed and served the same way.
"""

import asyncio
import json
from typing import Any, AsyncIterator, Dict, Mapping, Optional

from fastapi import Request
from fastapi.responses import StreamingResponse

#: How long an idle stream waits before sending a keep-alive.
PING_SECONDS = 20.0


def frame(data: Mapping[str, Any], event: Optional[str] = None) -> str:
    """Formats one SSE frame, optionally with a named event type."""
    prefix = f"event: {event}\n" if event else ""
    return f"{prefix}data: {json.dumps(data)}\n\n"


async def drain(
    request: Request, queue: "asyncio.Queue[Any]", ping: Dict[str, Any]
) -> AsyncIterator[str]:
    """Frames everything put on a queue until the client goes away, pinging while idle."""
    while not await request.is_disconnected():
        try:
            event = await asyncio.wait_for(queue.get(), timeout=PING_SECONDS)
        except asyncio.TimeoutError:
            event = ping
        yield frame(event)


def response(frames: AsyncIterator[str]) -> StreamingResponse:
    """Serves frames as an event stream that no cache or proxy holds back."""
    return StreamingResponse(
        frames,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
