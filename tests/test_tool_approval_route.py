"""Tests for recording a user's decision on a gated tool call.

The endpoint is reached with an id the model chose, not one Kayak did, and
providers do not agree on what an id may contain. Gemini's are thousands of
characters of base64 that routinely include "/", so an id in the URL path came
back as "Method Not Allowed": percent-encoding it does not survive routing, the
request misses the route, and it falls through to the frontend catch-all, which
serves GET only. These tests pin the ids that used to break it.
"""

import pytest
from fastapi.testclient import TestClient

from backend.app.agent.approvals import approval_registry
from backend.app.main import app

# Shortened from a real Gemini call id, keeping the parts that broke routing.
GEMINI_STYLE_ID = "call_1814813__thought__EqcQCqQQARFNMg8IkWfRd6gL/JPIYc9BS2E+kjkt/yOreq"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def clean_registry():
    yield
    approval_registry.cancel_conversation("conv_a")


class TestResolvingAnApproval:
    @pytest.mark.parametrize(
        "call_id",
        [
            "call_1",
            GEMINI_STYLE_ID,
            "call_with/slash",
            "call_with+plus_and=equals",
            "call_" + "x" * 4000,
        ],
    )
    def test_a_decision_reaches_the_registry_whatever_the_id_contains(
        self, client, call_id
    ):
        pending = approval_registry.register(
            call_id=call_id,
            conversation_id="conv_a",
            tool_name="run_command",
            arguments='{"command": "ls"}',
        )

        response = client.post(
            "/api/conversations/conv_a/tool-approvals",
            json={"call_id": call_id, "approved": True},
        )

        assert response.status_code == 200
        assert response.json() == {"status": "approved"}
        assert pending.approved is True

    def test_a_refusal_is_recorded_as_a_refusal(self, client):
        pending = approval_registry.register(
            call_id=GEMINI_STYLE_ID,
            conversation_id="conv_a",
            tool_name="run_command",
        )

        response = client.post(
            "/api/conversations/conv_a/tool-approvals",
            json={"call_id": GEMINI_STYLE_ID, "approved": False},
        )

        assert response.status_code == 200
        assert response.json() == {"status": "rejected"}
        assert pending.approved is False

    def test_deciding_on_a_call_nothing_is_waiting_for_is_a_404(self, client):
        response = client.post(
            "/api/conversations/conv_a/tool-approvals",
            json={"call_id": "never_registered", "approved": True},
        )

        assert response.status_code == 404
