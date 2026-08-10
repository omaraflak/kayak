from typing import Any, Dict, Optional
from fastapi import APIRouter
from pydantic import BaseModel
from backend.app.agent.sandbox import sandbox_manager
from backend.app.config import settings

router = APIRouter(prefix="/api/settings", tags=["settings"])


class UpdateSettingsRequest(BaseModel):
    """Payload for updating platform API keys, endpoints, and default model configuration."""
    DEFAULT_MODEL: Optional[str] = None
    OPENAI_API_KEY: Optional[str] = None
    GEMINI_API_KEY: Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None
    HUGGINGFACE_API_KEY: Optional[str] = None
    VLLM_API_BASE: Optional[str] = None
    OLLAMA_API_BASE: Optional[str] = None


@router.get("")
async def get_settings() -> Dict[str, Any]:
    """Retrieves current platform settings and credentials.

    Returns:
        Dictionary of platform configuration keys and Docker sandbox status.
    """
    return {
        "DEFAULT_MODEL": settings.DEFAULT_MODEL,
        "OPENAI_API_KEY": settings.OPENAI_API_KEY,
        "GEMINI_API_KEY": settings.GEMINI_API_KEY,
        "ANTHROPIC_API_KEY": settings.ANTHROPIC_API_KEY,
        "HUGGINGFACE_API_KEY": settings.HUGGINGFACE_API_KEY,
        "VLLM_API_BASE": settings.VLLM_API_BASE,
        "OLLAMA_API_BASE": settings.OLLAMA_API_BASE,
        "DOCKER_AVAILABLE": sandbox_manager.is_available,
    }


@router.post("")
async def update_settings(request: UpdateSettingsRequest) -> Dict[str, Any]:
    """Saves updated platform credentials to persistent config file.

    Args:
        request: UpdateSettingsRequest with updated key values.

    Returns:
        Status response containing updated settings.
    """
    updates = request.model_dump(exclude_unset=True)
    settings.save_settings(updates)
    return {
        "status": "saved",
        "settings": {
            "DEFAULT_MODEL": settings.DEFAULT_MODEL,
            "OPENAI_API_KEY": settings.OPENAI_API_KEY,
            "GEMINI_API_KEY": settings.GEMINI_API_KEY,
            "ANTHROPIC_API_KEY": settings.ANTHROPIC_API_KEY,
            "HUGGINGFACE_API_KEY": settings.HUGGINGFACE_API_KEY,
            "VLLM_API_BASE": settings.VLLM_API_BASE,
            "OLLAMA_API_BASE": settings.OLLAMA_API_BASE,
            "DOCKER_AVAILABLE": sandbox_manager.is_available,
        },
    }
