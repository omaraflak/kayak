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
from backend.app.routes import auth
from backend.app.routes.auth import (
    SESSION_COOKIE_NAME,
    TOKEN_HEADER_NAME,
    safe_next,
    session_value,
)

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
        assert response.cookies.get(SESSION_COOKIE_NAME) == session_value()

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


class TestTheBrowserCookie:
    """Browsers send a 127.0.0.1 cookie to every port, so it must not be the key."""

    def test_the_cookie_is_not_the_key(self, client):
        response = client.get("/api/auth/enter", params={"token": KEY})

        cookie = response.cookies.get(SESSION_COOKIE_NAME)
        assert cookie and cookie != KEY and KEY not in cookie

    def test_the_key_itself_is_no_session(self, client):
        client.cookies.set(SESSION_COOKIE_NAME, KEY)

        assert client.get("/api/agents").status_code == 401

    def test_pages_may_not_load_images_from_other_ports_on_this_mac(self, client):
        policy = client.get("/api/health").headers["content-security-policy"]

        # Kayak itself, inline data and HTTPS only: no http://127.0.0.1:<port>.
        assert "img-src 'self' data: blob: https:;" in policy
        assert "http:" not in policy


class TestOneTimeCodes:
    """How the launcher opens Kayak in the browser without putting the key in the address."""

    def test_only_the_key_holder_can_ask_for_a_code(self, client):
        assert client.post("/api/auth/codes", json={}).status_code == 401

    def test_a_code_signs_a_browser_in_once(self, client):
        code = client.post(
            "/api/auth/codes", json={}, headers={TOKEN_HEADER_NAME: KEY}
        ).json()["code"]
        assert KEY not in code

        first = client.get("/api/auth/enter", params={"code": code, "next": "/"})
        browser = TestClient(app, follow_redirects=False)
        second = browser.get("/api/auth/enter", params={"code": code, "next": "/"})

        assert first.cookies.get(SESSION_COOKIE_NAME) == session_value()
        # Seen in a process list or a browser's history, it is already spent.
        assert SESSION_COOKIE_NAME not in second.cookies
        assert browser.get("/api/agents").status_code == 401

    def test_a_code_expires(self, client, monkeypatch):
        code = client.post(
            "/api/auth/codes", json={}, headers={TOKEN_HEADER_NAME: KEY}
        ).json()["code"]
        later = auth.time.monotonic() + auth.CODE_LIFETIME_SECONDS + 1
        monkeypatch.setattr(auth.time, "monotonic", lambda: later)

        response = client.get("/api/auth/enter", params={"code": code})

        assert SESSION_COOKIE_NAME not in response.cookies

    def test_codes_cannot_pile_up(self, client):
        for _ in range(auth.MAX_OUTSTANDING_CODES + 10):
            client.post("/api/auth/codes", json={}, headers={TOKEN_HEADER_NAME: KEY})

        assert len(auth._codes) <= auth.MAX_OUTSTANDING_CODES

    def test_the_sign_in_outlasts_the_browser_session(self, client):
        response = client.get("/api/auth/enter", params={"token": KEY})

        # A bookmark keeps working after the browser restarts.
        assert "max-age=" in response.headers["set-cookie"].lower()


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
