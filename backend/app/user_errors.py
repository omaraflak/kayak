"""Failures explained for the person using Kayak, not for the person who wrote it.

Kayak is for people who do not code, so an error that reaches the screen has to say
what happened in everyday words and what to do next. Exception text does neither:
"litellm.AuthenticationError: Incorrect API key provided: sk-abc***def" is accurate,
and useless to someone who has never heard of LiteLLM.

The raw exception is never thrown away. Every ``describe_*`` function logs it before
returning its sentence, which is what puts it in the server log and so in the
support bundle that Report a problem sends.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

#: Shown when nothing more specific is known. Still says what to do.
GENERIC_FAILURE = (
    "Something went wrong inside Kayak. Try again. If it keeps happening, quit Kayak, "
    "open it again, and use Report a problem in Platform Settings."
)


class UserFacingError(Exception):
    """A failure whose message is already written for the person using Kayak.

    Raised by code that both requests and background work call, so one sentence
    serves both: the handler in ``main.py`` turns it into ``{"detail": message}``,
    and a job stores ``.message`` as its error. Raising ``HTTPException`` from such
    code instead put "409: " in front of every message a job recorded.
    """

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _mentions(error: BaseException, *markers: str) -> bool:
    """Whether an exception's text contains any of the markers, ignoring case."""
    text = f"{type(error).__name__} {error}".lower()
    return any(marker in text for marker in markers)


# ------------------------------------------------------------------ sandboxes


def describe_sandbox_error(error: BaseException) -> str:
    """The sentence for a conversation whose sandbox could not be started.

    Args:
        error: Whatever ``ensure_sandbox`` raised.

    Returns:
        str: What happened and what to do about it.
    """
    logger.warning("Could not start a sandbox: %s", error, exc_info=error)

    if isinstance(error, UserFacingError):
        return error.message
    if _mentions(error, "no space left", "errno 28", "disk quota"):
        return (
            "Docker Desktop is out of disk space, so the agent's sandbox can't start. "
            "Free some space on your Mac, then try again."
        )
    if _mentions(error, "out of memory", "cannot allocate memory", "oomkilled"):
        return (
            "There isn't enough free memory to start the agent's sandbox. Close some "
            "apps or stop a model you aren't using, then try again."
        )
    if _mentions(
        error,
        "docker is not running",
        "connection aborted",
        "connection refused",
        "docker.sock",
        "error while fetching server api version",
    ):
        return (
            "Kayak can't reach Docker Desktop, which the agent's sandbox runs in. Make "
            "sure Docker Desktop is open and running, then try again."
        )
    if _mentions(
        error,
        "pull access denied",
        "manifest unknown",
        "toomanyrequests",
        "tls handshake",
        "no such host",
        "i/o timeout",
        "dial tcp",
        "temporary failure in name resolution",
        "network is unreachable",
    ):
        return (
            "Kayak couldn't download the agent's sandbox. Check your internet "
            "connection, then try again."
        )
    return (
        "The agent's sandbox couldn't start. Try again. If it keeps happening, quit "
        "Docker Desktop, open it again, and retry."
    )


# --------------------------------------------------------------- model calls


def describe_llm_error(
    error: BaseException,
    provider_name: Optional[str],
    *,
    local: bool,
    answering: bool,
) -> str:
    """The sentence for a model call that failed.

    Dispatches on LiteLLM's exception classes, most specific first: several of them
    subclass others (a context-window error is a bad request, a timeout is a
    connection error), so the order is what keeps the specific explanation from
    being swallowed by the general one.

    Args:
        error: What the call raised.
        provider_name: Display name of the provider ("OpenAI"), None if unknown.
        local: Whether the model runs on this Mac.
        answering: Whether the model had already started its answer.

    Returns:
        str: What happened and what to do about it.
    """
    import litellm

    logger.warning(
        "Model call failed (%s): %s", provider_name or "unknown provider", error,
        exc_info=error,
    )

    if isinstance(error, UserFacingError):
        return error.message

    # Read from the text only on a bad request: a rate limit also talks about
    # "tokens", per minute, and is not cured by starting a new conversation.
    if isinstance(error, litellm.ContextWindowExceededError) or (
        isinstance(error, litellm.BadRequestError)
        and _mentions(error, "context length", "context window", "maximum context")
    ):
        return (
            "This conversation has grown too long for the agent's model. Start a new "
            "conversation to keep going."
        )

    if local:
        if answering:
            return (
                "The model on this Mac stopped while it was answering. Start it again "
                "from Local Models, then try again."
            )
        if isinstance(error, (litellm.APIConnectionError, litellm.NotFoundError)):
            return (
                "This agent's model isn't running on this Mac. Start it from Local "
                "Models, then try again."
            )
        return (
            "The model on this Mac couldn't answer. Try again. If it keeps happening, "
            "restart it from Local Models."
        )

    name = provider_name or "The AI service"

    if isinstance(error, litellm.AuthenticationError):
        return f"{name} didn't accept your key. Check it in Platform Settings, then try again."
    if isinstance(error, litellm.PermissionDeniedError):
        return (
            f"Your {name} account can't use this agent's model. Pick a different model "
            "for the agent in Agent Profiles."
        )
    if isinstance(error, litellm.RateLimitError):
        if _mentions(error, "quota", "credit", "billing", "insufficient"):
            return (
                f"Your {name} account is out of credit. Add credit on {name}'s website, "
                "then try again."
            )
        return f"{name} is getting too many requests right now. Wait a minute, then try again."
    if isinstance(error, litellm.ContentPolicyViolationError):
        return f"{name} wouldn't answer this message. Try rephrasing it."
    if isinstance(error, litellm.NotFoundError):
        return (
            f"{name} doesn't offer the model this agent uses. Pick a different model "
            "for the agent in Agent Profiles."
        )
    if isinstance(error, litellm.Timeout):
        return f"{name} took too long to answer. Try again."
    if isinstance(error, litellm.APIConnectionError):
        return f"Kayak couldn't reach {name}. Check your internet connection, then try again."
    if isinstance(
        error,
        (
            litellm.ServiceUnavailableError,
            litellm.InternalServerError,
            litellm.BadGatewayError,
        ),
    ):
        return f"{name} is having trouble right now. Try again in a few minutes."
    if isinstance(error, litellm.BadRequestError):
        if provider_name is None:
            return (
                "This agent's model isn't set up correctly. Choose a model for it in "
                "Agent Profiles."
            )
        return (
            f"{name} couldn't handle this request. Try again. If it keeps happening, "
            "start a new conversation."
        )
    return "Something went wrong while the agent was answering. Try again."
