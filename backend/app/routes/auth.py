"""Shared-secret authentication.

Authentication is active when ``KAYAK_AUTH_TOKEN`` is set, which the desktop launcher
always does, with a random key of its own. Being reachable only from this Mac is not
enough on its own: every container on Docker Desktop can reach the Mac's loopback
through host.docker.internal, so a model's own code or an agent's sandbox could
otherwise read conversations, start agents on the user's paid keys, or approve its
own tool calls. The agent has shell and filesystem access, so an open port is
equivalent to an open shell.

The token is exchanged for a session cookie because ``EventSource`` cannot send
custom headers. The launcher opens Kayak in the user's browser through
``/api/auth/enter`` with a one-time code it asked for with the key: the address
passes through ``open``, where any process can see it, and into browser history,
so it must not carry the key itself. A code is useless once used, and after two
minutes either way.
"""

import hashlib
import hmac
import secrets
import time
from typing import Any, Dict, Optional
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from backend.app.config import settings

router = APIRouter(prefix="/api/auth", tags=["auth"])

SESSION_COOKIE_NAME = "kayak_session"
TOKEN_HEADER_NAME = "X-Kayak-Token"

#: How long a sign-in code stays usable, and how many may be outstanding at once.
CODE_LIFETIME_SECONDS = 120.0
MAX_OUTSTANDING_CODES = 20

#: A year and a month: as long as browsers allow. The key behind it does not
#: change, so a bookmark keeps working without going back through the app.
SESSION_MAX_AGE_SECONDS = 400 * 24 * 60 * 60

#: One-time sign-in codes, with when each stops being valid.
_codes: Dict[str, float] = {}

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


def session_value() -> str:
    """What the session cookie holds: derived from the key, never the key itself.

    Browsers send a cookie for 127.0.0.1 to every port on it, not only Kayak's,
    and they keep it on disk. The key stays in the launcher's file and the
    server's environment.
    """
    return hmac.new(
        settings.AUTH_TOKEN.encode(), b"kayak-browser-session", hashlib.sha256
    ).hexdigest()


def matches_session(candidate: Optional[str]) -> bool:
    """Whether a session cookie is this server's, compared in constant time."""
    if not settings.AUTH_TOKEN or not candidate:
        return False
    return hmac.compare_digest(candidate.encode(), session_value().encode())


def is_authorized(request: Request) -> bool:
    """Reports whether a request carries the key in its header, or a session cookie."""
    if not settings.AUTH_TOKEN:
        return True
    return matches_token(request.headers.get(TOKEN_HEADER_NAME)) or matches_session(
        request.cookies.get(SESSION_COOKIE_NAME)
    )


def _set_session(response: Response) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_value(),
        httponly=True,
        samesite="lax",
        path="/",
        max_age=SESSION_MAX_AGE_SECONDS,
    )


def issue_code() -> str:
    """A new one-time sign-in code, forgetting expired and surplus ones."""
    now = time.monotonic()
    for stale in [code for code, expires in _codes.items() if expires <= now]:
        del _codes[stale]
    while len(_codes) >= MAX_OUTSTANDING_CODES:
        del _codes[next(iter(_codes))]
    code = secrets.token_urlsafe(32)
    _codes[code] = now + CODE_LIFETIME_SECONDS
    return code


def redeem_code(code: Optional[str]) -> bool:
    """Uses up a sign-in code, reporting whether it was valid."""
    if not code:
        return False
    expires = _codes.pop(code, None)
    return expires is not None and expires > time.monotonic()


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


@router.post("/codes")
async def create_code() -> Dict[str, str]:
    """A one-time code that signs a browser in, for whoever holds the key.

    Not public: the middleware has already required the key. This is how the
    launcher opens Kayak in the browser without putting the key in the address.
    """
    return {"code": issue_code()}


@router.get("/enter")
async def enter(
    code: Optional[str] = None, token: Optional[str] = None, next: Optional[str] = None
) -> RedirectResponse:
    """Signs a browser in and sends it on to the page it asked for.

    A navigation can carry neither a header nor a request body, so the proof is
    in the address: a one-time code from ``/codes``, or -- from the launcher
    before codes, which opened Kayak in its own window -- the key itself. A
    wrong, used or missing one signs nothing in, and the page asks for a key.
    """
    response = RedirectResponse(url=safe_next(next), status_code=303)
    # The address held a credential; nothing may keep a copy of this answer.
    response.headers["Cache-Control"] = "no-store"
    if settings.AUTH_TOKEN and (redeem_code(code) or matches_token(token)):
        _set_session(response)
    return response

