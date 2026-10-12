from typing import Dict, List, Optional

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, create_model

from backend.app import docker_host, support
from backend.app.config import settings
from backend.app.providers import PROVIDERS

router = APIRouter(prefix="/api/settings", tags=["settings"])

# Credentials are never returned in full: the settings endpoint is reachable by
# anything that can reach the UI, and echoing live provider keys back over it turns
# a page load into key exfiltration.
_MASK_CHAR = "•"
_VISIBLE_SUFFIX = 4

_SECRET_KEYS = tuple(provider.api_key_setting for provider in PROVIDERS)

#: Addresses that keep the server reachable only from this Mac.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


#: Payload for updating stored provider credentials, one optional field per provider.
#:
#: Credentials are the only thing this endpoint writes. Endpoints, model choices and
#: execution limits are deployment configuration and come from the environment, so
#: they cannot be changed by anyone who can reach the UI. Unknown fields are rejected
#: rather than ignored, so an attempt to set one fails loudly instead of appearing to
#: succeed.
UpdateSettingsRequest = create_model(
    "UpdateSettingsRequest",
    __config__=ConfigDict(extra="forbid"),
    **{key: (Optional[str], None) for key in _SECRET_KEYS},
)


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


def reachable_address() -> str:
    """The address Kayak can be reached on from outside this Mac, if any.

    Inside a container Kayak binds 0.0.0.0, which is harmless: what matters is the
    address Docker publishes the port on, which the launcher and docker-compose
    keep to 127.0.0.1.
    """
    hosts = docker_host.published_hosts(settings.PORT)
    if hosts is None:
        return settings.HOST
    exposed = [host for host in hosts if host not in LOOPBACK_HOSTS]
    return exposed[0] if exposed else "127.0.0.1"


def _security_posture() -> SecurityPosture:
    """Describes whether this server is safely reachable."""
    host = reachable_address()
    is_loopback = host in LOOPBACK_HOSTS
    auth_required = bool(settings.AUTH_TOKEN)

    warning: Optional[str] = None
    if not is_loopback and not auth_required:
        warning = (
            f"This server is reachable on {host} and has no access token, so anyone who "
            "can reach it can run shell commands on this Mac. Set KAYAK_AUTH_TOKEN, or "
            "publish it on 127.0.0.1 only."
        )
    elif not is_loopback:
        warning = (
            f"This server is reachable beyond this Mac (on {host}). Access is gated by "
            "the configured token."
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


class InstalledVersions(BaseModel):
    """Versions of the two separately updatable pieces."""
    kayak: str
    #: None when Kayak is running without the desktop launcher.
    launcher: Optional[str] = None


@router.get("/versions", response_model=InstalledVersions)
async def get_versions() -> InstalledVersions:
    """Reports what is installed on both sides of the container boundary."""
    installed = support.versions()
    return InstalledVersions(kayak=installed["kayak"], launcher=installed["launcher"])


@router.get("/support-bundle")
async def get_support_bundle() -> PlainTextResponse:
    """Returns a plain-text dump of everything useful for diagnosing a problem.

    Served as a download rather than shown in the page: it contains hundreds of
    log lines, and the point is to attach it to a message rather than read it.
    """
    return PlainTextResponse(
        support.build_bundle(),
        headers={"Content-Disposition": 'attachment; filename="kayak-support.txt"'},
    )
