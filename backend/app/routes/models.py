from dataclasses import dataclass
from typing import Dict, List, Optional
from fastapi import APIRouter, Query
import httpx
import litellm
from pydantic import BaseModel
from backend.app.config import settings

router = APIRouter(prefix="/api/models", tags=["models"])


class ModelItem(BaseModel):
    """Represents an individual LLM model option."""
    id: str
    name: str
    provider: str
    description: str
    context_window: Optional[str] = None
    is_available: bool = True
    is_running_locally: bool = False


class ProviderModels(BaseModel):
    """Groups available models by backend provider."""
    provider_id: str
    provider_name: str
    icon: str
    is_configured: bool
    status_message: str
    models: List[ModelItem]


class HuggingFaceModelSearchResult(BaseModel):
    """Represents a model returned from the Hugging Face Hub search."""
    id: str
    name: str
    downloads: int
    likes: int
    pipeline_tag: Optional[str] = None
    model_string_hf: str
    model_string_vllm: str


@dataclass(frozen=True)
class ModelEntry:
    """Static catalog entry for a curated model."""
    id: str
    name: str
    description: str
    default_context_window: str


@dataclass(frozen=True)
class ProviderCatalogItem:
    """Static catalog configuration for a model provider."""
    provider_id: str
    provider_name: str
    icon: str
    api_key_attr: str
    models: List[ModelEntry]


def _get_context_window(model_id: str, default: str) -> str:
    """Retrieves context window limit from LiteLLM model info."""
    try:
        info = litellm.get_model_info(model_id)
        max_tokens = info.get("max_input_tokens") or info.get("max_tokens")
        if max_tokens:
            return f"{max_tokens:,} tokens"
    except Exception:
        pass
    return default


_PROVIDER_CATALOG: List[ProviderCatalogItem] = [
    ProviderCatalogItem(
        provider_id="gemini",
        provider_name="Google",
        icon="✨",
        api_key_attr="GEMINI_API_KEY",
        models=[
            ModelEntry("gemini/gemini-2.5-pro", "Gemini 2.5 Pro", "Flagship model for complex agentic workflows, deep coding, and complex tool reasoning.", "2,000,000 tokens"),
            ModelEntry("gemini/gemini-2.5-flash", "Gemini 2.5 Flash", "Next-gen balanced frontier model with rapid response and multimodal reasoning.", "1,000,000 tokens"),
            ModelEntry("gemini/gemini-2.5-flash-thinking", "Gemini 2.5 Flash Thinking", "Integrated chain-of-thought reasoning model for difficult logic and multi-step tasks.", "1,000,000 tokens"),
            ModelEntry("gemini/gemini-3.6-flash", "Gemini 3.6 Flash", "Default ultra-fast agent synthesis engine with sub-second token latency.", "1,000,000 tokens"),
            ModelEntry("gemini/gemini-1.5-pro", "Gemini 1.5 Pro", "Massive context multimodal model for large codebase comprehension.", "2,000,000 tokens"),
            ModelEntry("gemini/gemini-1.5-flash", "Gemini 1.5 Flash", "High-frequency lightweight model optimized for rapid tool calling.", "1,000,000 tokens"),
        ],
    ),
    ProviderCatalogItem(
        provider_id="openai",
        provider_name="OpenAI",
        icon="🧠",
        api_key_attr="OPENAI_API_KEY",
        models=[
            ModelEntry("openai/gpt-4o", "GPT-4o", "OpenAI's flagship omni model for advanced reasoning and general agent workflows.", "128,000 tokens"),
            ModelEntry("openai/gpt-4o-mini", "GPT-4o Mini", "Fast and lightweight model offering high intelligence at low latency.", "128,000 tokens"),
            ModelEntry("openai/o1", "o1", "Deep reasoning model designed to think through math, code, and difficult logic.", "200,000 tokens"),
            ModelEntry("openai/o3-mini", "o3-mini", "High-speed reasoning model specialized for coding, math, and tool execution.", "200,000 tokens"),
            ModelEntry("openai/gpt-4-turbo", "GPT-4 Turbo", "High-capability model with 128k context and accurate function calling.", "128,000 tokens"),
        ],
    ),
    ProviderCatalogItem(
        provider_id="anthropic",
        provider_name="Anthropic",
        icon="⚡",
        api_key_attr="ANTHROPIC_API_KEY",
        models=[
            ModelEntry("anthropic/claude-3-7-sonnet-latest", "Claude 3.7 Sonnet", "Anthropic's flagship hybrid reasoning model for precision engineering.", "200,000 tokens"),
            ModelEntry("anthropic/claude-3-5-sonnet-20241022", "Claude 3.5 Sonnet", "Industry-leading precision coding and agentic execution model.", "200,000 tokens"),
            ModelEntry("anthropic/claude-3-5-haiku-20241022", "Claude 3.5 Haiku", "Near-instantaneous token generation with exceptional coding intelligence.", "200,000 tokens"),
            ModelEntry("anthropic/claude-3-opus-20240229", "Claude 3 Opus", "Deep analytical model for complex synthesis and writing tasks.", "200,000 tokens"),
        ],
    ),
]


def _build_provider_response(provider: ProviderCatalogItem) -> ProviderModels:
    """Constructs a ProviderModels API response from a catalog item and current settings."""
    has_key = bool(getattr(settings, provider.api_key_attr, ""))
    items = [
        ModelItem(
            id=entry.id,
            name=entry.name,
            provider=provider.provider_id,
            description=entry.description,
            context_window=_get_context_window(entry.id, entry.default_context_window),
            is_available=has_key,
            is_running_locally=False,
        )
        for entry in provider.models
    ]
    return ProviderModels(
        provider_id=provider.provider_id,
        provider_name=provider.provider_name,
        icon=provider.icon,
        is_configured=has_key,
        status_message="API Key Configured" if has_key else "Missing API Key in Settings",
        models=items,
    )


@router.get("", response_model=List[ProviderModels])
async def list_available_models() -> List[ProviderModels]:
    """Returns provider-specific curated models, active local servers, and configured API statuses."""
    return [_build_provider_response(p) for p in _PROVIDER_CATALOG]


@router.get("/huggingface/search", response_model=List[HuggingFaceModelSearchResult])
async def search_huggingface_models(
    query: str = Query(..., min_length=2, description="Search term for Hugging Face Hub")
) -> List[HuggingFaceModelSearchResult]:
    """Queries the Hugging Face Hub API for open-weight LLM models matching search query."""
    results: List[HuggingFaceModelSearchResult] = []
    url = f"https://huggingface.co/api/models?pipeline_tag=text-generation&search={query}&limit=16&sort=downloads&direction=-1"

    headers: Dict[str, str] = {}
    if settings.HUGGINGFACE_API_KEY:
        headers["Authorization"] = f"Bearer {settings.HUGGINGFACE_API_KEY}"

    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                for item in response.json():
                    model_id = item.get("id", "")
                    if model_id:
                        results.append(
                            HuggingFaceModelSearchResult(
                                id=model_id,
                                name=model_id,
                                downloads=item.get("downloads", 0),
                                likes=item.get("likes", 0),
                                pipeline_tag=item.get("pipeline_tag"),
                                model_string_hf=f"huggingface/{model_id}",
                                model_string_vllm=f"vllm/{model_id}",
                            )
                        )
    except Exception as error:
        print(f"Error querying Hugging Face API: {error}")

    return results
