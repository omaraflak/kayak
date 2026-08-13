from typing import Any, Dict, Optional
from fastapi import APIRouter
from pydantic import BaseModel
from backend.app.agent.sandbox import sandbox_manager
from backend.app.config import settings

router = APIRouter(prefix="/api/settings", tags=["settings"])

# Credentials are never returned in full: the settings endpoint is reachable by
# anything that can reach the UI, and echoing live provider keys back over it turns
# a page load into key exfiltration.
_MASK_CHAR = "•"
_VISIBLE_SUFFIX = 4

_SECRET_KEYS = (
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "ANTHROPIC_API_KEY",
    "HUGGINGFACE_API_KEY",
)


class UpdateSettingsRequest(BaseModel):
    """Payload for updating platform API keys, endpoints, and default model configuration."""
    DEFAULT_MODEL: Optional[str] = None
    OPENAI_API_KEY: Optional[str] = None
    GEMINI_API_KEY: Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None
    HUGGINGFACE_API_KEY: Optional[str] = None
    VLLM_API_BASE: Optional[str] = None


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


def _public_settings() -> Dict[str, Any]:
    """Builds the settings payload exposed to the UI, with credentials masked."""
    payload: Dict[str, Any] = {
        "DEFAULT_MODEL": settings.DEFAULT_MODEL,
        "VLLM_API_BASE": settings.VLLM_API_BASE,
        "DOCKER_AVAILABLE": sandbox_manager.is_available,
    }
    for key in _SECRET_KEYS:
        value = getattr(settings, key, "")
        payload[key] = mask_secret(value)
        payload[f"{key}_SET"] = bool(value)
    return payload


@router.get("")
async def get_settings() -> Dict[str, Any]:
    """Retrieves current platform settings, with API keys masked.

    Returns:
        Dictionary of platform configuration keys and Docker sandbox status.
    """
    return _public_settings()


@router.post("")
async def update_settings(request: UpdateSettingsRequest) -> Dict[str, Any]:
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
