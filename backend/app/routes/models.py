import asyncio
from typing import Any, Dict, List, Optional
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
    model_string_ollama: str


def _get_context_window(model_id: str) -> Optional[str]:
    """Retrieves context window limit from LiteLLM model info."""
    try:
        info = litellm.get_model_info(model_id)
        max_tokens = info.get("max_input_tokens") or info.get("max_tokens")
        if max_tokens:
            return f"{max_tokens:,} tokens"
    except Exception:
        pass
    return None


@router.get("", response_model=List[ProviderModels])
async def list_available_models() -> List[ProviderModels]:
    """Inspects configured API keys, probes local servers, and queries litellm.models_by_provider.

    Returns:
        A list of ProviderModels grouped by provider.
    """
    providers: List[ProviderModels] = []
    models_by_provider = getattr(litellm, "models_by_provider", {})

    # 1. Google Gemini (from models_by_provider['gemini'] or fallback)
    has_gemini_key = bool(settings.GEMINI_API_KEY)
    gemini_raw_models = models_by_provider.get("gemini", [
        "gemini-3.6-flash",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-1.5-pro",
        "gemini-1.5-flash",
    ])
    gemini_items: List[ModelItem] = []
    for model_name in gemini_raw_models:
        model_id = model_name if model_name.startswith("gemini/") else f"gemini/{model_name}"
        clean_name = model_name.replace("gemini/", "").replace("-", " ").title()
        gemini_items.append(
            ModelItem(
                id=model_id,
                name=f"Gemini {clean_name}",
                provider="gemini",
                description=f"Google {clean_name} model via Gemini API.",
                context_window=_get_context_window(model_id) or "1,000,000 tokens",
                is_available=has_gemini_key,
            )
        )

    providers.append(
        ProviderModels(
            provider_id="gemini",
            provider_name="Google Gemini",
            icon="✨",
            is_configured=has_gemini_key,
            status_message="API Key Configured" if has_gemini_key else "Missing API Key in Settings",
            models=gemini_items,
        )
    )

    # 2. OpenAI (from models_by_provider['openai'] or fallback)
    has_openai_key = bool(settings.OPENAI_API_KEY)
    openai_raw_models = models_by_provider.get("openai", [
        "gpt-4o",
        "gpt-4o-mini",
        "o1",
        "o3-mini",
        "gpt-4-turbo",
    ])
    openai_items: List[ModelItem] = []
    # Filter to chat-capable models
    for model_name in openai_raw_models:
        if any(skip in model_name for skip in ["whisper", "tts", "dall-e", "embedding", "davinci", "babbage"]):
            continue
        model_id = model_name if model_name.startswith("openai/") else f"openai/{model_name}"
        openai_items.append(
            ModelItem(
                id=model_id,
                name=model_name.replace("openai/", "").upper(),
                provider="openai",
                description=f"OpenAI {model_name} model for conversational reasoning.",
                context_window=_get_context_window(model_id) or "128,000 tokens",
                is_available=has_openai_key,
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

    # 3. Anthropic (from models_by_provider['anthropic'] or fallback)
    has_anthropic_key = bool(settings.ANTHROPIC_API_KEY)
    anthropic_raw_models = models_by_provider.get("anthropic", [
        "claude-3-5-sonnet-20241022",
        "claude-3-5-haiku-20241022",
        "claude-3-opus-20240229",
    ])
    anthropic_items: List[ModelItem] = []
    for model_name in anthropic_raw_models:
        model_id = model_name if model_name.startswith("anthropic/") else f"anthropic/{model_name}"
        clean_name = model_name.replace("anthropic/", "").replace("-", " ").title()
        anthropic_items.append(
            ModelItem(
                id=model_id,
                name=clean_name,
                provider="anthropic",
                description=f"Anthropic Claude model ({clean_name}) for precision coding and agent reasoning.",
                context_window=_get_context_window(model_id) or "200,000 tokens",
                is_available=has_anthropic_key,
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

    # 4. Local Ollama Server Probe (from live server or models_by_provider['ollama'])
    ollama_models: List[ModelItem] = []
    ollama_connected = False
    ollama_status = f"Unreachable at {settings.OLLAMA_API_BASE}"

    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            response = await client.get(f"{settings.OLLAMA_API_BASE.rstrip('/')}/api/tags")
            if response.status_code == 200:
                data = response.json()
                models_list = data.get("models", [])
                ollama_connected = True
                ollama_status = f"Connected ({len(models_list)} local models installed)"
                for model_entry in models_list:
                    model_name = model_entry.get("name", "")
                    if model_name:
                        ollama_models.append(
                            ModelItem(
                                id=f"ollama/{model_name}",
                                name=f"Ollama: {model_name}",
                                provider="ollama",
                                description=f"Locally running Ollama model ({model_entry.get('details', {}).get('parameter_size', 'local')}).",
                                context_window="Local context",
                                is_available=True,
                                is_running_locally=True,
                            )
                        )
    except Exception:
        pass

    if not ollama_models:
        # Fallback to litellm.models_by_provider['ollama']
        ollama_catalog = models_by_provider.get("ollama", ["llama3", "qwen2.5-coder:7b", "mistral"])
        for model_name in ollama_catalog:
            model_id = model_name if model_name.startswith("ollama/") else f"ollama/{model_name}"
            ollama_models.append(
                ModelItem(
                    id=model_id,
                    name=f"Ollama: {model_name.replace('ollama/', '')}",
                    provider="ollama",
                    description="Open-weight model running locally via Ollama.",
                    context_window="Local context",
                    is_available=ollama_connected,
                )
            )

    providers.append(
        ProviderModels(
            provider_id="ollama",
            provider_name="Local Ollama",
            icon="🦙",
            is_configured=ollama_connected,
            status_message=ollama_status,
            models=ollama_models,
        )
    )

    # 5. Local vLLM Server Probe
    vllm_models: List[ModelItem] = []
    vllm_connected = False
    vllm_status = f"Unreachable at {settings.VLLM_API_BASE}"

    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            response = await client.get(f"{settings.VLLM_API_BASE.rstrip('/')}/models")
            if response.status_code == 200:
                data = response.json()
                models_list = data.get("data", [])
                vllm_connected = True
                vllm_status = f"Connected ({len(models_list)} served models active)"
                for model_entry in models_list:
                    model_id = model_entry.get("id", "")
                    if model_id:
                        vllm_models.append(
                            ModelItem(
                                id=f"vllm/{model_id}",
                                name=f"vLLM: {model_id.split('/')[-1]}",
                                provider="vllm",
                                description=f"Locally served vLLM model ({model_id}) with high-throughput PagedAttention.",
                                context_window="Local GPU context",
                                is_available=True,
                                is_running_locally=True,
                            )
                        )
    except Exception:
        pass

    if not vllm_models:
        vllm_models = [
            ModelItem(
                id="vllm/meta-llama/Meta-Llama-3-8B-Instruct",
                name="Meta Llama 3 8B Instruct",
                provider="vllm",
                description="Served by local vLLM cluster with OpenAI-compatible API.",
                context_window="8,000 tokens",
                is_available=vllm_connected,
            ),
            ModelItem(
                id="vllm/Qwen/Qwen2.5-Coder-7B-Instruct",
                name="Qwen 2.5 Coder 7B Instruct",
                provider="vllm",
                description="High-performance open weights served with local vLLM.",
                context_window="32,000 tokens",
                is_available=vllm_connected,
            ),
        ]

    providers.append(
        ProviderModels(
            provider_id="vllm",
            provider_name="Local vLLM Server",
            icon="🚀",
            is_configured=vllm_connected,
            status_message=vllm_status,
            models=vllm_models,
        )
    )

    # 6. Hugging Face (from models_by_provider['huggingface'] or Hub)
    has_hf_key = bool(settings.HUGGINGFACE_API_KEY)
    hf_raw_models = models_by_provider.get("huggingface", [
        "Qwen/Qwen2.5-Coder-7B-Instruct",
        "meta-llama/Meta-Llama-3.1-8B-Instruct",
        "mistralai/Mistral-7B-Instruct-v0.3",
        "deepseek-ai/DeepSeek-Coder-V2-Lite-Instruct",
    ])
    hf_items: List[ModelItem] = []
    for model_name in hf_raw_models:
        model_id = model_name if model_name.startswith("huggingface/") else f"huggingface/{model_name}"
        clean_name = model_name.replace("huggingface/", "")
        hf_items.append(
            ModelItem(
                id=model_id,
                name=clean_name.split("/")[-1],
                provider="huggingface",
                description=f"Hugging Face repository: {clean_name}",
                context_window="Open Weights",
                is_available=True,
            )
        )

    providers.append(
        ProviderModels(
            provider_id="huggingface",
            provider_name="Hugging Face",
            icon="🤗",
            is_configured=has_hf_key,
            status_message="API Token Set" if has_hf_key else "Free Serverless / Local Deployment",
            models=hf_items,
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
                        short_name = model_id.split("/")[-1]
                        results.append(
                            HuggingFaceModelSearchResult(
                                id=model_id,
                                name=model_id,
                                downloads=item.get("downloads", 0),
                                likes=item.get("likes", 0),
                                pipeline_tag=item.get("pipeline_tag"),
                                model_string_hf=f"huggingface/{model_id}",
                                model_string_vllm=f"vllm/{model_id}",
                                model_string_ollama=f"ollama/{short_name}",
                            )
                        )
    except Exception as error:
        print(f"Error querying Hugging Face API: {error}")

    return results
