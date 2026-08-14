from typing import Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from backend.app.config import settings
from backend.app.providers import PROVIDERS

router = APIRouter(prefix="/api/settings", tags=["settings"])

# Credentials are never returned in full: the settings endpoint is reachable by
# anything that can reach the UI, and echoing live provider keys back over it turns
# a page load into key exfiltration.
_MASK_CHAR = "•"
_VISIBLE_SUFFIX = 4

_SECRET_KEYS = tuple(provider.api_key_setting for provider in PROVIDERS)

#: Addresses that keep the server reachable only from this machine.
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class UpdateSettingsRequest(BaseModel):
    """Payload for updating stored provider credentials.

    Credentials are the only thing this endpoint writes. Endpoints, model choices and
    execution limits are deployment configuration and come from the environment, so
    they cannot be changed by anyone who can reach the UI. Unknown fields are rejected
    rather than ignored, so an attempt to set one fails loudly instead of appearing to
    succeed.
    """

    model_config = ConfigDict(extra="forbid")

    OPENAI_API_KEY: Optional[str] = None
    GEMINI_API_KEY: Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None
    HUGGINGFACE_API_KEY: Optional[str] = None


class ProviderCredential(BaseModel):
    """A provider and the state of its stored credential."""
    id: str
    name: str
    icon: str
    setting_key: str
    console_url: str
    key_hint: str
    #: Masked preview identifying the stored key without disclosing it.
    preview: str
    is_set: bool


class SecurityPosture(BaseModel):
    """How exposed this server is.

    Kayak gives agents shell and filesystem access, so whether the API is
    authenticated and who can reach it is the most consequential thing on this page.
    """
    auth_required: bool
    host: str
    is_loopback: bool
    #: Present when the combination of bind address and auth is unsafe.
    warning: Optional[str] = None


class PublicSettings(BaseModel):
    """Everything the settings page renders, with credentials masked."""
    providers: List[ProviderCredential]
    security: SecurityPosture


def mask_secret(value: str) -> str:
    """Renders a credential as a preview that identifies it without disclosing it."""
    if not value:
        return ""
    if len(value) <= _VISIBLE_SUFFIX:
        return _MASK_CHAR * 8
    return _MASK_CHAR * 8 + value[-_VISIBLE_SUFFIX:]


def is_masked(value: str) -> bool:
    """Reports whether a submitted value is an unchanged mask rather than a new secret."""
    return _MASK_CHAR in value


def _security_posture() -> SecurityPosture:
    """Describes whether this server is safely reachable."""
    host = settings.HOST
    is_loopback = host in _LOOPBACK_HOSTS
    auth_required = bool(settings.AUTH_TOKEN)

    warning: Optional[str] = None
    if not is_loopback and not auth_required:
        warning = (
            f"This server is bound to {host} and has no access token, so anyone who can "
            "reach it can run shell commands on this machine. Set KAYAK_AUTH_TOKEN, or "
            "bind to 127.0.0.1."
        )
    elif not is_loopback:
        warning = (
            f"This server is reachable beyond this machine (bound to {host}). Access is "
            "gated by the configured token."
        )

    return SecurityPosture(
        auth_required=auth_required,
        host=host,
        is_loopback=is_loopback,
        warning=warning,
    )


def _public_settings() -> PublicSettings:
    """Builds the settings payload exposed to the UI, with credentials masked."""
    credentials = []
    for provider in PROVIDERS:
        value = getattr(settings, provider.api_key_setting, "")
        credentials.append(
            ProviderCredential(
                id=provider.id,
                name=provider.name,
                icon=provider.icon,
                setting_key=provider.api_key_setting,
                console_url=provider.console_url,
                key_hint=provider.key_hint,
                preview=mask_secret(value),
                is_set=bool(value),
            )
        )

    return PublicSettings(providers=credentials, security=_security_posture())


@router.get("", response_model=PublicSettings)
async def get_settings() -> PublicSettings:
    """Retrieves current platform settings, with API keys masked."""
    return _public_settings()


@router.post("", response_model=Dict[str, object])
async def update_settings(request: UpdateSettingsRequest) -> Dict[str, object]:
    """Saves updated platform credentials to persistent config file.

    Values still holding their masked preview are treated as unchanged, so saving an
    unrelated setting cannot overwrite a live key with its own mask.

    Args:
        request: UpdateSettingsRequest with updated key values.

    Returns:
        Status response containing the updated (masked) settings.
    """
    updates = {
        key: value
        for key, value in request.model_dump(exclude_unset=True).items()
        if not (key in _SECRET_KEYS and isinstance(value, str) and is_masked(value))
    }
    settings.save_settings(updates)
    return {"status": "saved", "settings": _public_settings()}

