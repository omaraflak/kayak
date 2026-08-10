import asyncio
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Query
import httpx
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


@router.get("", response_model=List[ProviderModels])
async def list_available_models() -> List[ProviderModels]:
    """Inspects configured API keys, probes local Ollama and vLLM servers, and returns all available models.

    Returns:
        A list of ProviderModels grouped by provider.
    """
    providers: List[ProviderModels] = []

    # 1. Google Gemini
    has_gemini_key = bool(settings.GEMINI_API_KEY)
    providers.append(
        ProviderModels(
            provider_id="gemini",
            provider_name="Google Gemini",
            icon="✨",
            is_configured=has_gemini_key,
            status_message="API Key Configured" if has_gemini_key else "Missing API Key in Settings",
            models=[
                ModelItem(
                    id="gemini/gemini-3.6-flash",
                    name="Gemini 3.6 Flash",
                    provider="gemini",
                    description="Ultra-fast flagship model optimized for coding, autonomous tool loops, and agent tasks.",
                    context_window="1,000,000 tokens",
                    is_available=has_gemini_key,
                ),
                ModelItem(
                    id="gemini/gemini-2.5-pro",
                    name="Gemini 2.5 Pro",
                    provider="gemini",
                    description="High-intelligence reasoning and complex problem-solving model.",
                    context_window="2,000,000 tokens",
                    is_available=has_gemini_key,
                ),
                ModelItem(
                    id="gemini/gemini-2.5-flash",
                    name="Gemini 2.5 Flash",
                    provider="gemini",
                    description="Lightweight multimodal model for high-throughput interactions.",
                    context_window="1,000,000 tokens",
                    is_available=has_gemini_key,
                ),
            ],
        )
    )

    # 2. OpenAI
    has_openai_key = bool(settings.OPENAI_API_KEY)
    providers.append(
        ProviderModels(
            provider_id="openai",
            provider_name="OpenAI",
            icon="🧠",
            is_configured=has_openai_key,
            status_message="API Key Configured" if has_openai_key else "Missing API Key in Settings",
            models=[
                ModelItem(
                    id="openai/gpt-4o",
                    name="GPT-4o",
                    provider="openai",
                    description="High-intelligence multimodal flagship model for multi-step agent actions.",
                    context_window="128,000 tokens",
                    is_available=has_openai_key,
                ),
                ModelItem(
                    id="openai/gpt-4o-mini",
                    name="GPT-4o Mini",
                    provider="openai",
                    description="Affordable and fast model for lightweight execution and subagents.",
                    context_window="128,000 tokens",
                    is_available=has_openai_key,
                ),
                ModelItem(
                    id="openai/o1",
                    name="o1",
                    provider="openai",
                    description="Deep reasoning model with self-correction and extended thinking.",
                    context_window="200,000 tokens",
                    is_available=has_openai_key,
                ),
                ModelItem(
                    id="openai/o3-mini",
                    name="o3-mini",
                    provider="openai",
                    description="Fast reasoning model optimized for STEM, math, and code synthesis.",
                    context_window="200,000 tokens",
                    is_available=has_openai_key,
                ),
            ],
        )
    )

    # 3. Anthropic
    has_anthropic_key = bool(settings.ANTHROPIC_API_KEY)
    providers.append(
        ProviderModels(
            provider_id="anthropic",
            provider_name="Anthropic",
            icon="⚡",
            is_configured=has_anthropic_key,
            status_message="API Key Configured" if has_anthropic_key else "Missing API Key in Settings",
            models=[
                ModelItem(
                    id="anthropic/claude-3-5-sonnet-20241022",
                    name="Claude 3.5 Sonnet",
                    provider="anthropic",
                    description="Industry benchmark for agentic coding, computer use, and precise instructions.",
                    context_window="200,000 tokens",
                    is_available=has_anthropic_key,
                ),
                ModelItem(
                    id="anthropic/claude-3-5-haiku-20241022",
                    name="Claude 3.5 Haiku",
                    provider="anthropic",
                    description="Extremely fast, lightweight model for high-frequency tool use.",
                    context_window="200,000 tokens",
                    is_available=has_anthropic_key,
                ),
            ],
        )
    )

    # 4. Local Ollama Server Probe
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
        ollama_models = [
            ModelItem(
                id="ollama/llama3",
                name="Llama 3 (8B)",
                provider="ollama",
                description="Meta open-weight model running locally via Ollama.",
                context_window="8,000 tokens",
                is_available=ollama_connected,
            ),
            ModelItem(
                id="ollama/qwen2.5-coder:7b",
                name="Qwen 2.5 Coder (7B)",
                provider="ollama",
                description="Alibaba open-source code specialist running locally via Ollama.",
                context_window="32,000 tokens",
                is_available=ollama_connected,
            ),
        ]

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

    # 6. Hugging Face
    has_hf_key = bool(settings.HUGGINGFACE_API_KEY)
    providers.append(
        ProviderModels(
            provider_id="huggingface",
            provider_name="Hugging Face",
            icon="🤗",
            is_configured=has_hf_key,
            status_message="API Token Set" if has_hf_key else "Free Serverless / Local Deployment",
            models=[
                ModelItem(
                    id="huggingface/Qwen/Qwen2.5-Coder-7B-Instruct",
                    name="Qwen 2.5 Coder 7B",
                    provider="huggingface",
                    description="Leading open-source coding model on Hugging Face.",
                    context_window="32,000 tokens",
                    is_available=True,
                ),
                ModelItem(
                    id="huggingface/meta-llama/Meta-Llama-3.1-8B-Instruct",
                    name="Llama 3.1 8B Instruct",
                    provider="huggingface",
                    description="Open-weights flagship model for versatile reasoning and tooling.",
                    context_window="128,000 tokens",
                    is_available=True,
                ),
                ModelItem(
                    id="huggingface/mistralai/Mistral-7B-Instruct-v0.3",
                    name="Mistral 7B Instruct v0.3",
                    provider="huggingface",
                    description="Compact open model supporting native tool calling and function schemas.",
                    context_window="32,000 tokens",
                    is_available=True,
                ),
                ModelItem(
                    id="huggingface/deepseek-ai/DeepSeek-Coder-V2-Lite-Instruct",
                    name="DeepSeek Coder V2 Lite",
                    provider="huggingface",
                    description="High-efficiency mixture-of-experts model for software development.",
                    context_window="64,000 tokens",
                    is_available=True,
                ),
            ],
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
