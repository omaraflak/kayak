"""Shared-secret authentication.

Authentication is active when ``KAYAK_AUTH_TOKEN`` is set, which the desktop launcher
always does, with a random key of its own. Being reachable only from this Mac is not
enough on its own: every container on Docker Desktop can reach the Mac's loopback
through host.docker.internal, so a model's own code or an agent's sandbox could
otherwise read conversations, start agents on the user's paid keys, or approve its
own tool calls. The agent has shell and filesystem access, so an open port is
equivalent to an open shell.

The token is exchanged for a session cookie because ``EventSource`` cannot send
custom headers. The launcher signs its window in through ``/api/auth/enter``, the one
place a token travels in a query string: it is answered with a redirect that drops
it, so it is never kept in history, and it only ever crosses loopback.
"""

import hmac
from typing import Any, Dict, Optional
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from backend.app.config import settings

router = APIRouter(prefix="/api/auth", tags=["auth"])

SESSION_COOKIE_NAME = "kayak_session"
TOKEN_HEADER_NAME = "X-Kayak-Token"

# Endpoints reachable without a token: the auth handshake itself, the probe used to
# discover whether authentication is required at all, and the health check the
# launcher polls to know the server is up -- which it does before it signs in.
PUBLIC_API_PATHS = frozenset(
    {"/api/auth/session", "/api/auth/status", "/api/auth/enter", "/api/health"}
)


class SessionRequest(BaseModel):
    """Payload exchanging a shared secret for a session cookie."""
    token: str


def matches_token(candidate: Optional[str]) -> bool:
    """Whether a presented secret is the configured one, compared in constant time."""
    expected = settings.AUTH_TOKEN
    if not expected or not candidate:
        return False
    return hmac.compare_digest(candidate.encode(), expected.encode())


def is_authorized(request: Request) -> bool:
    """Reports whether a request carries a valid token, by header or session cookie."""
    if not settings.AUTH_TOKEN:
        return True
    return matches_token(request.headers.get(TOKEN_HEADER_NAME)) or matches_token(
        request.cookies.get(SESSION_COOKIE_NAME)
    )


def _set_session(response: Response) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=settings.AUTH_TOKEN,
        httponly=True,
        samesite="lax",
        path="/",
    )


def safe_next(target: Optional[str]) -> str:
    """Where to go after signing in, kept to a page of this server.

    Only a plain path is followed: anything that could leave the site --
    "//elsewhere.example", a full URL, a backslash browsers read as a slash --
    lands on the start page instead.
    """
    if not target or not target.startswith("/") or target.startswith("//"):
        return "/"
    if "\\" in target or any(ord(character) < 32 for character in target):
        return "/"
    return target


@router.get("/status")
async def auth_status(request: Request) -> Dict[str, Any]:
    """Reports whether authentication is enabled and whether this caller is signed in."""
    return {
        "auth_required": bool(settings.AUTH_TOKEN),
        "authenticated": is_authorized(request),
    }


@router.post("/session")
async def create_session(payload: SessionRequest, response: Response) -> Dict[str, Any]:
    """Validates a shared secret and issues a session cookie.

    Raises:
        HTTPException: If the supplied token does not match the configured secret.
    """
    if not settings.AUTH_TOKEN:
        return {"status": "auth_disabled"}

    if not matches_token(payload.token):
        raise HTTPException(status_code=401, detail="That key wasn't accepted.")

    _set_session(response)
    return {"status": "authenticated"}


@router.get("/enter")
async def enter(token: Optional[str] = None, next: Optional[str] = None) -> RedirectResponse:
    """Signs a window in with the key and sends it on to the page it asked for.

    How the launcher opens the Kayak window: a navigation can carry neither a
    header nor a request body. A wrong or missing key signs nothing in, and the
    page it lands on asks for one.
    """
    response = RedirectResponse(url=safe_next(next), status_code=303)
    # The address held the key; nothing may keep a copy of this answer.
    response.headers["Cache-Control"] = "no-store"
    if settings.AUTH_TOKEN and matches_token(token):
        _set_session(response)
    return response

