"""Tests that failures reach the user as sentences they can act on.

Kayak is used by people who do not code. Every message here used to be an exception
string -- "litellm.AuthenticationError: Incorrect API key provided", "(500 Server Error
for http+docker://...)", "Internal Server Error" -- which is accurate and useless to
them. What is pinned down is that each common failure gets its own explanation with a
next step, and that the raw text never leaks into it.
"""

from pathlib import Path

import httpx
import litellm
import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from backend.app import llm
from backend.app.inference import registry as inference_registry
from backend.app.inference.models import ServerState
from backend.app.main import app
from backend.app.user_errors import (
    GENERIC_FAILURE,
    UserFacingError,
    describe_llm_error,
    describe_sandbox_error,
)


def _llm_error(cls, message: str = "provider said no"):
    if cls is litellm.PermissionDeniedError:
        response = httpx.Response(403, request=httpx.Request("POST", "https://example.com"))
        return cls(message=message, llm_provider="openai", model="gpt-x", response=response)
    return cls(message=message, llm_provider="openai", model="gpt-x")


def _cloud(error) -> str:
    return describe_llm_error(error, "OpenAI", local=False, answering=False)


class TestModelCallFailures:
    @pytest.mark.parametrize(
        "cls, expected",
        [
            (litellm.AuthenticationError, "didn't accept your key"),
            (litellm.PermissionDeniedError, "can't use this agent's model"),
            (litellm.RateLimitError, "too many requests"),
            (litellm.ContentPolicyViolationError, "wouldn't answer"),
            (litellm.NotFoundError, "doesn't offer the model"),
            (litellm.Timeout, "took too long"),
            (litellm.APIConnectionError, "couldn't reach OpenAI"),
            (litellm.ServiceUnavailableError, "having trouble"),
            (litellm.InternalServerError, "having trouble"),
            (litellm.BadGatewayError, "having trouble"),
            (litellm.BadRequestError, "couldn't handle this request"),
        ],
    )
    def test_each_kind_of_failure_has_its_own_explanation(self, cls, expected):
        message = _cloud(_llm_error(cls))

        assert expected in message
        assert "provider said no" not in message
        assert "litellm" not in message.lower()

    def test_a_timeout_is_not_explained_as_a_lost_connection(self):
        # Timeout subclasses the connection error; the order of the checks is what
        # keeps the more specific sentence.
        assert "took too long" in _cloud(_llm_error(litellm.Timeout))

    def test_a_conversation_that_outgrew_the_model_says_to_start_a_new_one(self):
        message = _cloud(_llm_error(litellm.ContextWindowExceededError))

        assert "new conversation" in message

    def test_an_exhausted_account_is_told_apart_from_a_busy_provider(self):
        message = _cloud(_llm_error(litellm.RateLimitError, "You exceeded your current quota"))

        assert "out of credit" in message

    def test_a_rate_limit_about_tokens_is_not_mistaken_for_a_long_conversation(self):
        message = _cloud(
            _llm_error(litellm.RateLimitError, "Rate limit reached on tokens per min (TPM)")
        )

        assert "new conversation" not in message

    def test_a_local_model_that_never_answered_is_not_running(self):
        message = describe_llm_error(
            _llm_error(litellm.APIConnectionError), None, local=True, answering=False
        )

        assert "isn't running" in message
        assert "Local Models" in message

    def test_a_local_model_that_died_mid_answer_says_so(self):
        message = describe_llm_error(
            _llm_error(litellm.APIConnectionError), None, local=True, answering=True
        )

        assert "stopped while it was answering" in message

    def test_something_unrecognised_still_says_what_to_do(self):
        message = _cloud(RuntimeError("segfault in libfoo"))

        assert "Try again" in message
        assert "libfoo" not in message


class TestSandboxFailures:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("Docker is not running. Start Docker Desktop and try again.", "Docker Desktop"),
            ("('Connection aborted.', ConnectionRefusedError(61, 'Connection refused'))", "Docker Desktop"),
            ("500 Server Error: no space left on device", "out of disk space"),
            ("dial tcp: lookup registry-1.docker.io: no such host", "internet connection"),
            ("cannot allocate memory", "enough free memory"),
            ("something nobody anticipated", "couldn't start"),
        ],
    )
    def test_each_cause_gets_its_own_explanation(self, raw, expected):
        message = describe_sandbox_error(RuntimeError(raw))

        assert expected in message
        assert "http+docker" not in message
        assert "container" not in message.lower()


class TestStartingATurn:
    def test_a_local_model_that_is_not_running_is_refused_up_front(self, monkeypatch):
        monkeypatch.setattr(
            inference_registry.text_manager,
            "get_status",
            lambda: type("S", (), {"state": ServerState.STOPPED, "model_id": None})(),
        )

        reason = llm.turn_blocker("vllm/Qwen/Qwen3-0.6B")

        assert reason is not None
        assert "Qwen3-0.6B isn't running" in reason

    def test_the_running_local_model_is_let_through(self, monkeypatch):
        monkeypatch.setattr(
            inference_registry.text_manager,
            "get_status",
            lambda: type("S", (), {"state": ServerState.READY, "model_id": "Qwen/Qwen3-0.6B"})(),
        )

        assert llm.turn_blocker("vllm/Qwen/Qwen3-0.6B") is None

    def test_a_different_local_model_running_is_not_enough(self, monkeypatch):
        monkeypatch.setattr(
            inference_registry.text_manager,
            "get_status",
            lambda: type("S", (), {"state": ServerState.READY, "model_id": "other/Model"})(),
        )

        assert llm.turn_blocker("vllm/Qwen/Qwen3-0.6B") is not None


# A router mounted only for these tests, so the handlers are exercised end to end.
_probe = APIRouter(prefix="/api/test-probe")


@_probe.get("/user-facing")
async def _raise_user_facing():
    raise UserFacingError("Give the skill a name first.", 400)


@_probe.get("/crash")
async def _raise_crash():
    raise RuntimeError("KeyError: 'model_id' in manager.py line 412")


@_probe.get("/typed")
async def _typed(count: int):
    return {"count": count}


# Ahead of the frontend's catch-all route, which would otherwise answer first.
_existing = len(app.router.routes)
app.include_router(_probe)
_added = app.router.routes[_existing:]
del app.router.routes[_existing:]
app.router.routes[0:0] = _added


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


class TestResponses:
    def test_a_message_written_for_the_user_is_sent_as_it_is(self, client):
        response = client.get("/api/test-probe/user-facing")

        assert response.status_code == 400
        assert response.json() == {"detail": "Give the skill a name first."}

    def test_a_crash_answers_with_a_sentence_not_the_exception(self, client):
        response = client.get("/api/test-probe/crash")

        assert response.status_code == 500
        assert response.json() == {"detail": GENERIC_FAILURE}
        assert "KeyError" not in response.text

    def test_a_malformed_request_is_not_answered_with_a_validation_dump(self, client):
        response = client.get("/api/test-probe/typed?count=lots")

        assert response.status_code == 422
        detail = response.json()["detail"]
        assert isinstance(detail, str)
        assert "Reload" in detail

    def test_an_empty_skill_name_is_refused_rather_than_written_to_the_skills_root(
        self, client, tmp_path: Path, monkeypatch
    ):
        from backend.app.config import settings

        monkeypatch.setattr(settings, "SKILLS_DIR", tmp_path / "skills")
        response = client.post(
            "/api/skills", json={"name": "   ", "description": "d", "instructions": "i"}
        )

        assert response.status_code == 400
        assert "name" in response.json()["detail"]
        assert not (tmp_path / "skills" / "SKILL.md").exists()
