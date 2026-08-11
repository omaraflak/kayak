import asyncio
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Query
import httpx
import litellm
from pydantic import BaseModel
from backend.app.config import settings
from backend.app.vllm.manager import vllm_manager
from backend.app.vllm.models import VLLMServerState

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


@router.get("", response_model=List[ProviderModels])
async def list_available_models() -> List[ProviderModels]:
    """Returns provider-specific curated models, active local servers, and configured API statuses.

    Returns:
        A list of ProviderModels distinctly separated by provider.
    """
    providers: List[ProviderModels] = []

    # =========================================================================
    # 1. Google Gemini
    # =========================================================================
    has_gemini_key = bool(settings.GEMINI_API_KEY)
    gemini_curated = [
        ("gemini/gemini-2.5-pro", "Gemini 2.5 Pro", "Flagship model for complex agentic workflows, deep coding, and complex tool reasoning.", "2,000,000 tokens"),
        ("gemini/gemini-2.5-flash", "Gemini 2.5 Flash", "Next-gen balanced frontier model with rapid response and multimodal reasoning.", "1,000,000 tokens"),
        ("gemini/gemini-2.5-flash-thinking", "Gemini 2.5 Flash Thinking", "Integrated chain-of-thought reasoning model for difficult logic and multi-step tasks.", "1,000,000 tokens"),
        ("gemini/gemini-3.6-flash", "Gemini 3.6 Flash", "Default ultra-fast agent synthesis engine with sub-second token latency.", "1,000,000 tokens"),
        ("gemini/gemini-1.5-pro", "Gemini 1.5 Pro", "Massive context multimodal model for large codebase comprehension.", "2,000,000 tokens"),
        ("gemini/gemini-1.5-flash", "Gemini 1.5 Flash", "High-frequency lightweight model optimized for rapid tool calling.", "1,000,000 tokens"),
    ]
    gemini_items: List[ModelItem] = []
    for mid, name, desc, ctx in gemini_curated:
        gemini_items.append(
            ModelItem(
                id=mid,
                name=name,
                provider="gemini",
                description=desc,
                context_window=_get_context_window(mid, ctx),
                is_available=has_gemini_key,
                is_running_locally=False,
            )
        )
    providers.append(
        ProviderModels(
            provider_id="gemini",
            provider_name="Google",
            icon="✨",
            is_configured=has_gemini_key,
            status_message="API Key Configured" if has_gemini_key else "Missing API Key in Settings",
            models=gemini_items,
        )
    )

    # =========================================================================
    # 2. OpenAI
    # =========================================================================
    has_openai_key = bool(settings.OPENAI_API_KEY)
    openai_curated = [
        ("openai/gpt-4o", "GPT-4o", "OpenAI's flagship omni model for advanced reasoning and general agent workflows.", "128,000 tokens"),
        ("openai/gpt-4o-mini", "GPT-4o Mini", "Fast and lightweight model offering high intelligence at low latency.", "128,000 tokens"),
        ("openai/o1", "o1", "Deep reasoning model designed to think through math, code, and difficult logic.", "200,000 tokens"),
        ("openai/o3-mini", "o3-mini", "High-speed reasoning model specialized for coding, math, and tool execution.", "200,000 tokens"),
        ("openai/gpt-4-turbo", "GPT-4 Turbo", "High-capability model with 128k context and accurate function calling.", "128,000 tokens"),
    ]
    openai_items: List[ModelItem] = []
    for mid, name, desc, ctx in openai_curated:
        openai_items.append(
            ModelItem(
                id=mid,
                name=name,
                provider="openai",
                description=desc,
                context_window=_get_context_window(mid, ctx),
                is_available=has_openai_key,
                is_running_locally=False,
            )
        )
    providers.append(
        ProviderModels(
            provider_id="openai",
            provider_name="OpenAI",
            icon="🧠",
            is_configured=has_openai_key,
            status_message="API Key Configured" if has_openai_key else "Missing API Key in Settings",
            models=openai_items,
        )
    )

    # =========================================================================
    # 3. Anthropic
    # =========================================================================
    has_anthropic_key = bool(settings.ANTHROPIC_API_KEY)
    anthropic_curated = [
        ("anthropic/claude-3-7-sonnet-latest", "Claude 3.7 Sonnet", "Anthropic's flagship hybrid reasoning model for precision engineering.", "200,000 tokens"),
        ("anthropic/claude-3-5-sonnet-20241022", "Claude 3.5 Sonnet", "Industry-leading precision coding and agentic execution model.", "200,000 tokens"),
        ("anthropic/claude-3-5-haiku-20241022", "Claude 3.5 Haiku", "Near-instantaneous token generation with exceptional coding intelligence.", "200,000 tokens"),
        ("anthropic/claude-3-opus-20240229", "Claude 3 Opus", "Deep analytical model for complex synthesis and writing tasks.", "200,000 tokens"),
    ]
    anthropic_items: List[ModelItem] = []
    for mid, name, desc, ctx in anthropic_curated:
        anthropic_items.append(
            ModelItem(
                id=mid,
                name=name,
                provider="anthropic",
                description=desc,
                context_window=_get_context_window(mid, ctx),
                is_available=has_anthropic_key,
                is_running_locally=False,
            )
        )
    providers.append(
        ProviderModels(
            provider_id="anthropic",
            provider_name="Anthropic",
            icon="⚡",
            is_configured=has_anthropic_key,
            status_message="API Key Configured" if has_anthropic_key else "Missing API Key in Settings",
            models=anthropic_items,
        )
    )

    return providers


@router.get("/huggingface/search", response_model=List[HuggingFaceModelSearchResult])
async def search_huggingface_models(
    query: str = Query(..., min_length=2, description="Search term for Hugging Face Hub")
) -> List[HuggingFaceModelSearchResult]:
    """Queries the Hugging Face Hub API for open-weight LLM models matching search query.

    Args:
        query: Model keyword or organization (e.g., 'qwen', 'llama-3', 'mistral').

    Returns:
        List of matching models with download stats and formatted model strings.
    """
    results: List[HuggingFaceModelSearchResult] = []
    url = f"https://huggingface.co/api/models?pipeline_tag=text-generation&search={query}&limit=16&sort=downloads&direction=-1"

    headers = {}
    if settings.HUGGINGFACE_API_KEY:
        headers["Authorization"] = f"Bearer {settings.HUGGINGFACE_API_KEY}"

    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                models_data = response.json()
                for item in models_data:
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
