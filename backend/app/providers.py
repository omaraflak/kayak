"""The model providers Kayak knows about.

One registry, because the same four providers were previously described in three
places: a catalog in the models route, a hardcoded list of labelled inputs in the
settings page, and the mapping from model prefix to API key in the LLM layer. Adding a
provider meant editing all three and the settings form by hand.
"""

from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class ProviderInfo:
    """A provider's identity, credential, and where to get one."""

    id: str
    name: str
    icon: str
    #: Name of the attribute on `settings` holding this provider's credential.
    api_key_setting: str
    #: Where the user obtains a key.
    console_url: str
    #: Shape of the credential, shown as the input placeholder.
    key_hint: str
    #: Whether this credential unlocks models for agents to run on.
    serves_models: bool = True


PROVIDERS: Tuple[ProviderInfo, ...] = (
    ProviderInfo(
        id="gemini",
        name="Google Gemini",
        icon="✨",
        api_key_setting="GEMINI_API_KEY",
        console_url="https://aistudio.google.com/app/apikey",
        key_hint="AIzaSy...",
    ),
    ProviderInfo(
        id="openai",
        name="OpenAI",
        icon="🧠",
        api_key_setting="OPENAI_API_KEY",
        console_url="https://platform.openai.com/api-keys",
        key_hint="sk-proj-...",
    ),
    ProviderInfo(
        id="anthropic",
        name="Anthropic",
        icon="⚡",
        api_key_setting="ANTHROPIC_API_KEY",
        console_url="https://console.anthropic.com/settings/keys",
        key_hint="sk-ant-...",
    ),
    ProviderInfo(
        id="huggingface",
        name="Hugging Face",
        icon="🤗",
        api_key_setting="HUGGINGFACE_API_KEY",
        console_url="https://huggingface.co/settings/tokens",
        key_hint="hf_...",
        serves_models=False,
    ),
)

_BY_ID: Dict[str, ProviderInfo] = {provider.id: provider for provider in PROVIDERS}

#: Settings attribute holding the credential for each provider id.
API_KEY_SETTINGS: Dict[str, str] = {
    provider.id: provider.api_key_setting for provider in PROVIDERS
}


def get_provider(provider_id: str) -> Optional[ProviderInfo]:
    """Looks up a provider by id, or None if it is not one Kayak knows."""
    return _BY_ID.get(provider_id)


def require_provider(provider_id: str) -> ProviderInfo:
    """Looks up a provider that the caller knows exists.

    Used where a provider id is written in the source rather than supplied by a
    request, so a typo fails at import instead of silently producing a catalog entry
    with no identity.

    Raises:
        KeyError: If the id is not registered.
    """
    provider = _BY_ID.get(provider_id)
    if provider is None:
        raise KeyError(f"Unknown provider id '{provider_id}'.")
    return provider
