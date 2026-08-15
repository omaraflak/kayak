"""Tests that provider credentials come from the Settings page and nowhere else.

Kayak is meant to be installed and opened rather than configured beforehand, so keys
are typed into the Settings page and stored in data/settings.json. Reading them from
the environment as well would give the app two sources of truth: the page could report
a provider as unconfigured while calls to it quietly succeeded on an exported variable,
and a key removed in the UI would keep working until the shell was restarted.
"""

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.app.config import Settings
from backend.app.database import init_db
from backend.app.llm import missing_key_error
from backend.app.models import CreateConversationRequest
from backend.app.providers import PROVIDERS
from backend.app.routes import conversations as conversations_route


ENV_KEYS = [provider.api_key_setting for provider in PROVIDERS]


class TestCredentialsIgnoreTheEnvironment:
    @pytest.mark.parametrize("key", ENV_KEYS)
    def test_an_exported_key_is_not_picked_up(self, key: str, monkeypatch, tmp_path):
        monkeypatch.setenv(key, "sk-from-the-shell")
        monkeypatch.setattr("backend.app.config.SETTINGS_FILE", tmp_path / "none.json")

        assert getattr(Settings(), key) == ""

    def test_the_settings_file_is_the_source(self, monkeypatch, tmp_path: Path):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"GEMINI_API_KEY": "AIzaFromTheUI"}))
        monkeypatch.setenv("GEMINI_API_KEY", "AIzaFromTheShell")
        monkeypatch.setattr("backend.app.config.SETTINGS_FILE", settings_file)

        loaded = Settings()

        assert loaded.GEMINI_API_KEY == "AIzaFromTheUI"

    def test_starting_with_no_configuration_at_all_works(self, monkeypatch, tmp_path):
        # The installed-and-opened case: no .env, no settings file, no exported keys.
        for key in ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setattr("backend.app.config.SETTINGS_FILE", tmp_path / "none.json")

        loaded = Settings()

        assert all(getattr(loaded, key) == "" for key in ENV_KEYS)


class TestMissingKeyError:
    def test_a_configured_provider_is_allowed_through(self, monkeypatch):
        monkeypatch.setattr("backend.app.llm.settings.GEMINI_API_KEY", "AIza-set")

        assert missing_key_error("gemini/gemini-3.6-flash") is None

    def test_an_unconfigured_provider_is_named_and_points_at_settings(self, monkeypatch):
        monkeypatch.setattr("backend.app.llm.settings.ANTHROPIC_API_KEY", "")

        message = missing_key_error("anthropic/claude-sonnet-4")

        assert message is not None
        assert "Anthropic" in message
        assert "Settings" in message

    def test_the_huggingface_alias_resolves_to_a_readable_name(self, monkeypatch):
        # LiteLLM accepts "hf/", which is not the id used in the provider registry.
        monkeypatch.setattr("backend.app.llm.settings.HUGGINGFACE_API_KEY", "")

        message = missing_key_error("hf/mistralai/Mistral-7B")

        assert message is not None
        assert "Hugging Face" in message

    def test_a_local_model_needs_no_credential(self):
        assert missing_key_error("vllm/Qwen/Qwen2.5-7B-Instruct") is None

    def test_an_unrecognised_model_is_left_to_litellm(self):
        # Refusing here would block provider strings Kayak does not have a registry
        # entry for but LiteLLM understands perfectly well.
        assert missing_key_error("ollama/llama3") is None


class TestTurnsAreRefusedWithoutAKey:
    """A turn that cannot run is rejected in the response, not only on the stream.

    Without a key the failure is instant -- fast enough that on a fresh install the
    turn finished before the browser had subscribed to the conversation's events, so
    the error reached nobody and the prompt sat there unanswered.
    """

    async def test_creating_a_conversation_with_a_prompt_is_refused(self, monkeypatch):
        await init_db()
        monkeypatch.setattr("backend.app.llm.settings.GEMINI_API_KEY", "")
        monkeypatch.setattr(
            conversations_route, "resolve_conversation_model",
            lambda agent_id: "gemini/gemini-3.6-flash",
        )

        with pytest.raises(HTTPException) as excinfo:
            await conversations_route.create_new_conversation(
                CreateConversationRequest(agent_id="general", initial_message="hi")
            )

        assert excinfo.value.status_code == 400
        assert "Settings" in excinfo.value.detail

    async def test_nothing_is_created_by_the_refused_request(self, monkeypatch):
        await init_db()
        monkeypatch.setattr("backend.app.llm.settings.GEMINI_API_KEY", "")
        monkeypatch.setattr(
            conversations_route, "resolve_conversation_model",
            lambda agent_id: "gemini/gemini-3.6-flash",
        )

        before = {c.id for c in await conversations_route.get_all_conversations()}
        with pytest.raises(HTTPException):
            await conversations_route.create_new_conversation(
                CreateConversationRequest(agent_id="general", initial_message="hi")
            )
        after = {c.id for c in await conversations_route.get_all_conversations()}

        # No container is started and no dead row is left in the sidebar.
        assert after == before

    async def test_a_configured_key_is_not_refused(self, monkeypatch):
        monkeypatch.setattr("backend.app.llm.settings.GEMINI_API_KEY", "AIza-set")
        monkeypatch.setattr(
            conversations_route, "resolve_conversation_model",
            lambda agent_id: "gemini/gemini-3.6-flash",
        )

        assert conversations_route.require_provider_key("general") is None
