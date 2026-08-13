"""Optional shared-secret authentication.

Authentication is only active when ``KAYAK_AUTH_TOKEN`` is set. It exists for the
case where Kayak is reachable beyond loopback: the agent has shell and filesystem
access, so an open port is equivalent to an open shell.

The token is exchanged for a session cookie because ``EventSource`` cannot send
custom headers, and passing a credential in the query string would leak it into
browser history, proxy logs, and referrers.
"""

from typing import Any, Dict
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from backend.app.config import settings

router = APIRouter(prefix="/api/auth", tags=["auth"])

SESSION_COOKIE_NAME = "kayak_session"
TOKEN_HEADER_NAME = "X-Kayak-Token"

# Endpoints reachable without a token: the auth handshake itself, and the health
# probe used to discover whether authentication is required at all.
PUBLIC_API_PATHS = frozenset({"/api/auth/session", "/api/auth/status"})


class SessionRequest(BaseModel):
    """Payload exchanging a shared secret for a session cookie."""
    token: str


def is_authorized(request: Request) -> bool:
    """Reports whether a request carries a valid token, by header or session cookie."""
    expected = settings.AUTH_TOKEN
    if not expected:
        return True
    if request.headers.get(TOKEN_HEADER_NAME) == expected:
        return True
    return request.cookies.get(SESSION_COOKIE_NAME) == expected


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

    if payload.token != settings.AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=settings.AUTH_TOKEN,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return {"status": "authenticated"}


@router.delete("/session")
async def destroy_session(response: Response) -> Dict[str, str]:
    """Clears the session cookie."""
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return {"status": "signed_out"}
