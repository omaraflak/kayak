"""Tests that only holders of the access key can use Kayak's server.

Every container on Docker Desktop can reach the Mac's loopback, so "only reachable
from this Mac" also meant "reachable from every model and sandbox". The launcher now
gives the server a random key and signs its own window in with it; these tests pin
down that nothing else gets through, and that signing in cannot be turned into a way
off the site.
"""

import pytest
from fastapi.testclient import TestClient

from backend.app.config import settings
from backend.app.main import app
from backend.app.routes.auth import SESSION_COOKIE_NAME, TOKEN_HEADER_NAME, safe_next

KEY = "k" * 64


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.setattr(settings, "AUTH_TOKEN", KEY)
    return TestClient(app, follow_redirects=False)


class TestWithAKey:
    def test_a_request_without_the_key_is_refused(self, client):
        # What a model's code or an agent's sandbox would send.
        assert client.get("/api/conversations").status_code == 401

    def test_a_wrong_key_is_refused(self, client):
        response = client.get("/api/conversations", headers={TOKEN_HEADER_NAME: "k" * 63})

        assert response.status_code == 401

    def test_the_key_lets_a_request_through(self, client):
        response = client.get("/api/agents", headers={TOKEN_HEADER_NAME: KEY})

        assert response.status_code == 200

    def test_the_health_check_stays_open_for_the_launcher(self, client):
        # Polled before the launcher signs in; a refusal read as "not up yet".
        assert client.get("/api/health").status_code == 200


class TestSigningIn:
    def test_the_right_key_signs_the_window_in_and_moves_on(self, client):
        response = client.get(
            "/api/auth/enter", params={"token": KEY, "next": "/?fresh=1-0"}
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/?fresh=1-0"
        assert response.headers["cache-control"] == "no-store"
        assert response.cookies.get(SESSION_COOKIE_NAME) == KEY

    def test_the_session_it_starts_is_enough_on_its_own(self, client):
        client.get("/api/auth/enter", params={"token": KEY})

        assert client.get("/api/agents").status_code == 200

    def test_a_wrong_key_signs_nothing_in(self, client):
        response = client.get("/api/auth/enter", params={"token": "nope", "next": "/"})

        assert response.status_code == 303
        assert SESSION_COOKIE_NAME not in response.cookies
        assert client.get("/api/agents").status_code == 401

    def test_without_a_key_configured_it_just_moves_on(self, monkeypatch):
        monkeypatch.setattr(settings, "AUTH_TOKEN", "")
        response = TestClient(app, follow_redirects=False).get(
            "/api/auth/enter", params={"next": "/chat"}
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/chat"


class TestWhereSigningInMayLead:
    @pytest.mark.parametrize(
        "target",
        [
            None,
            "",
            "https://elsewhere.example/",
            "//elsewhere.example/",
            "/\\elsewhere.example",
            "/chat\r\nSet-Cookie: x=y",
            "chat",
        ],
    )
    def test_anything_that_could_leave_the_site_lands_on_the_start_page(self, target):
        assert safe_next(target) == "/"

    def test_a_page_of_this_site_is_kept(self):
        assert safe_next("/c/abc?fresh=12-3") == "/c/abc?fresh=12-3"
