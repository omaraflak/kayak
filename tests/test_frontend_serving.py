"""Tests for how the backend serves the frontend build.

The headers here are load-bearing: the app shell used to go out with no
Cache-Control at all, so browsers and the launcher's webview applied heuristic
caching and kept showing a previous release's UI after an update. The version
label underneath came from the API and said the right thing, which made the
mismatch invisible until a feature was missing.
"""

import pytest
from fastapi.testclient import TestClient

from backend.app.main import FRONTEND_DIST, app

pytestmark = pytest.mark.skipif(
    not (FRONTEND_DIST / "index.html").exists(),
    reason="frontend build not present",
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestShellCaching:
    def test_the_app_shell_always_revalidates(self, client):
        response = client.get("/")

        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-cache"
        assert "text/html" in response.headers["content-type"]

    def test_every_spa_path_gets_the_same_no_cache_shell(self, client):
        # Each path is a separate cache entry in the browser; one of them going
        # out cacheable is enough to resurrect an old UI later.
        for path in ("/models", "/conversations", "/settings"):
            response = client.get(path)
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-cache", path

    def test_root_files_are_served_as_themselves_not_as_the_shell(self, client):
        # /favicon.svg used to come back as index.html, which left the browser
        # tab with the default globe instead of the app icon.
        response = client.get("/favicon.svg")

        assert response.status_code == 200
        assert "svg" in response.headers["content-type"]
        assert response.headers["cache-control"] == "no-cache"

    def test_fingerprinted_assets_are_immutable(self, client):
        shell = client.get("/").text
        import re

        match = re.search(r"/assets/[\w.-]+\.js", shell)
        assert match, "expected the shell to reference a fingerprinted script"

        response = client.get(match.group(0))

        assert response.status_code == 200
        assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


class TestServingBoundaries:
    def test_api_paths_are_never_answered_with_the_shell(self, client):
        # An API 404 answered with index.html reads as a 200 to every caller,
        # which is how a health probe once mistook Kayak itself for vLLM.
        response = client.get("/api/definitely/not/a/route")

        assert response.status_code == 404

    def test_paths_cannot_escape_the_frontend_build(self, client):
        response = client.get("/../pytest.ini")

        # Whatever normalization happens, the repository file must not come back.
        assert "[pytest]" not in response.text

    def test_an_unknown_spa_path_still_loads_the_app(self, client):
        response = client.get("/some/deep/route")

        assert response.status_code == 200
        assert "<div id=\"root\">" in response.text
