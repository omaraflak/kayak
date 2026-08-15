"""Tests for how provider credentials are stored and described.

The settings file holds API keys in plaintext, so how it is written matters as much as
what it contains: it was previously created with the default umask, leaving every
account on the machine able to read the user's keys.
"""

import json
from pathlib import Path
import stat

import pytest

from backend.app.config import SECRET_FILE_MODE, _write_private_json, settings
from backend.app.providers import PROVIDERS, get_provider, require_provider
from backend.app.routes.settings import (
    _security_posture,
    is_masked,
    mask_secret,
)


class TestPrivateWrite:
    def test_the_file_is_readable_only_by_its_owner(self, tmp_path: Path):
        target = tmp_path / "settings.json"

        _write_private_json(target, {"OPENAI_API_KEY": "sk-secret"})

        mode = stat.S_IMODE(target.stat().st_mode)
        assert mode == SECRET_FILE_MODE
        assert not mode & stat.S_IRGRP
        assert not mode & stat.S_IROTH

    def test_the_contents_round_trip(self, tmp_path: Path):
        target = tmp_path / "settings.json"
        payload = {"DEFAULT_MODEL": "gemini/gemini-2.5-flash", "OPENAI_API_KEY": "sk-x"}

        _write_private_json(target, payload)

        assert json.loads(target.read_text()) == payload

    def test_a_failed_write_leaves_no_temporary_file_behind(self, tmp_path: Path, monkeypatch):
        target = tmp_path / "settings.json"

        def explode(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr("backend.app.config.os.replace", explode)
        with pytest.raises(OSError):
            _write_private_json(target, {"a": "b"})

        assert list(tmp_path.iterdir()) == []

    def test_an_existing_file_is_replaced_not_appended(self, tmp_path: Path):
        target = tmp_path / "settings.json"
        _write_private_json(target, {"DEFAULT_MODEL": "first"})
        _write_private_json(target, {"DEFAULT_MODEL": "second"})

        assert json.loads(target.read_text()) == {"DEFAULT_MODEL": "second"}


class TestMasking:
    def test_a_preview_identifies_a_key_without_disclosing_it(self):
        preview = mask_secret("sk-proj-abcdefghijkl9876")

        assert preview.endswith("9876")
        assert "abcdefghijkl" not in preview

    def test_a_short_secret_discloses_nothing_at_all(self):
        assert mask_secret("abc") == "•" * 8

    def test_an_absent_key_has_no_preview(self):
        assert mask_secret("") == ""

    def test_a_preview_submitted_back_is_recognised_as_unchanged(self):
        assert is_masked(mask_secret("sk-proj-abcdefghijkl9876")) is True

    def test_a_real_key_is_not_mistaken_for_a_preview(self):
        assert is_masked("sk-proj-abcdefghijkl9876") is False


class TestSecurityPosture:
    def test_a_loopback_server_needs_no_warning(self, monkeypatch):
        monkeypatch.setattr(settings, "HOST", "127.0.0.1")
        monkeypatch.setattr(settings, "AUTH_TOKEN", "")

        posture = _security_posture()

        assert posture.is_loopback is True
        assert posture.auth_required is False
        assert posture.warning is None

    def test_an_open_bind_without_a_token_is_called_out(self, monkeypatch):
        # This is the configuration that hands shell access to anyone who can route
        # to the machine, so it must not be reported quietly.
        monkeypatch.setattr(settings, "HOST", "0.0.0.0")
        monkeypatch.setattr(settings, "AUTH_TOKEN", "")

        posture = _security_posture()

        assert posture.is_loopback is False
        assert posture.warning is not None
        assert "shell commands" in posture.warning

    def test_an_open_bind_with_a_token_is_noted_but_not_alarming(self, monkeypatch):
        monkeypatch.setattr(settings, "HOST", "0.0.0.0")
        monkeypatch.setattr(settings, "AUTH_TOKEN", "hunter2")

        posture = _security_posture()

        assert posture.auth_required is True
        assert posture.warning is not None
        assert "shell commands" not in posture.warning


class TestProviderRegistry:
    def test_every_provider_names_a_real_settings_attribute(self):
        for provider in PROVIDERS:
            assert hasattr(settings, provider.api_key_setting), provider.id

    def test_provider_ids_are_unique(self):
        ids = [provider.id for provider in PROVIDERS]
        assert len(ids) == len(set(ids))

    def test_every_provider_tells_the_user_where_to_get_a_key(self):
        for provider in PROVIDERS:
            assert provider.console_url.startswith("https://"), provider.id
            assert provider.key_hint, provider.id

    def test_an_unknown_id_is_reported(self):
        assert get_provider("not-a-provider") is None
        with pytest.raises(KeyError):
            require_provider("not-a-provider")
