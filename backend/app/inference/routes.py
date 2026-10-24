import asyncio
import logging
from typing import Dict, List

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from backend.app import sse
from backend.app.config import settings
from backend.app.inference import failures, metal, registry
from backend.app.inference.cache import CachePathError
from backend.app.inference.manager import metal_unavailable_message
from backend.app.inference.models import (
    DeployRequest,
    DeploymentProgress,
    HostCapability,
    MetalStatus,
    Modality,
    ModelCacheInfo,
    ModelClassification,
    RuntimeDescriptor,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/inference", tags=["inference"])


def _manager(modality: Modality):
    """The manager for a modality, or a 404 naming what is actually served."""
    try:
        return registry.manager_for(modality)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail="Kayak can't run this kind of model on this Mac.",
        )


@router.get("/runtimes", response_model=List[RuntimeDescriptor])
async def list_runtimes() -> List[RuntimeDescriptor]:
    """Describes every local runtime: what it serves and what it can be told.

    Clients read the catalogue filters, the "this runtime can load it" test and the
    set of settings to offer from here, so adding a runtime never means editing a
    client.
    """
    return registry.describe_runtimes()


@router.get("/classify", response_model=ModelClassification)
async def classify_model(repo_id: str = Query(..., min_length=1)) -> ModelClassification:
    """Which local runtime should serve a repository, and whether it can be fetched.

    Asked before starting a model whose task is not already known -- a cached model
    is only a name and a size on disk. Answered from the Hub rather than guessed from
    the name: starting a cached Kokoro used to go to vLLM, which spent minutes on a
    model it could never load.

    It also answers the question a start would otherwise answer the slow way: a model
    whose makers require accepting their terms, or one that does not exist, used to
    fail only after the engine and part of the weights had downloaded. Such a model
    comes back with ``message`` set, saying what to do instead.
    """
    url = f"https://huggingface.co/api/models/{repo_id}"
    token = settings.HUGGINGFACE_API_KEY or ""
    headers: Dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                try:
                    payload = response.json()
                except ValueError:
                    return ModelClassification(repo_id=repo_id, unknown=True)
                if not isinstance(payload, dict):
                    return ModelClassification(repo_id=repo_id, unknown=True)
                result = registry.classify(
                    repo_id,
                    pipeline_tag=payload.get("pipeline_tag"),
                    library_name=payload.get("library_name"),
                    tags=[tag for tag in (payload.get("tags") or []) if isinstance(tag, str)],
                )
                if result.runtime_key == "vllm" and only_gguf_weights(payload.get("siblings")):
                    result.supported = False
                    result.message = failures.WRONG_FORMAT
                    return result
                if payload.get("gated"):
                    result.gated = True
                    await _check_gated_access(client, result, token)
                return result
    except httpx.HTTPError:
        # Unreachable is not the same as unsupported. The caller falls back rather
        # than refusing to start a model because the Hub was down.
        return ModelClassification(repo_id=repo_id, unknown=True)

    if response.status_code == 404:
        return ModelClassification(
            repo_id=repo_id, not_found=True, message=failures.NOT_FOUND
        )
    if response.status_code == 401:
        # With a token, a 401 is the token being refused. Without one, Hugging Face
        # answers 401 alike for a private repository and one that does not exist,
        # and the "not found" sentence covers both.
        if token:
            return ModelClassification(
                repo_id=repo_id, needs_token=True, message=failures.TOKEN_REJECTED
            )
        return ModelClassification(
            repo_id=repo_id, not_found=True, message=failures.NOT_FOUND
        )
    return ModelClassification(repo_id=repo_id, unknown=True)


def only_gguf_weights(siblings: object) -> bool:
    """Whether a repository holds GGUF files and nothing the text engine can load.

    The Hub tags such a repository as an ordinary text-generation model, so it was
    offered a Start button that downloaded the engine and started it only to fail
    on the missing config.json. Its file list says so before anything downloads.
    """
    if not isinstance(siblings, list):
        return False
    names = [
        entry.get("rfilename")
        for entry in siblings
        if isinstance(entry, dict) and isinstance(entry.get("rfilename"), str)
    ]
    has_gguf = any(name.lower().endswith(".gguf") for name in names)
    return has_gguf and "config.json" not in names


async def _check_gated_access(
    client: httpx.AsyncClient, result: ModelClassification, token: str
) -> None:
    """Fills in whether this Mac may download a gated model, and what to say if not.

    The model's own page cannot say whether this particular account has accepted
    the terms; asking for one of its files can. Anything inconclusive is left alone:
    a start that might work is not refused on a guess.
    """
    if not token:
        result.needs_token = True
        result.message = failures.GATED_NEEDS_TOKEN
        return
    try:
        probe = await client.head(
            f"https://huggingface.co/{result.repo_id}/resolve/main/config.json",
            headers={"Authorization": f"Bearer {token}"},
            follow_redirects=False,
        )
    except httpx.HTTPError:
        return
    if probe.status_code == 401:
        result.needs_token = True
        result.message = failures.TOKEN_REJECTED
    elif probe.status_code == 403:
        result.message = failures.GATED_NO_ACCESS


@router.get("/status", response_model=Dict[str, DeploymentProgress])
async def get_all_status() -> Dict[str, DeploymentProgress]:
    """The current state of every local server, keyed by modality."""
    statuses = await asyncio.gather(
        *(manager.check_and_sync_status() for manager in registry.managers())
    )
    return {status.modality.value: status for status in statuses}


@router.get("/hardware", response_model=HostCapability)
async def get_host_capability() -> HostCapability:
    """Reports what Docker can give a model container, and whether vLLM is pulled."""
    return await registry.text_manager.get_host_capability()


@router.get("/metal", response_model=MetalStatus)
async def get_metal_status() -> MetalStatus:
    """Reports Metal inference as the desktop launcher currently sees it."""
    return metal.read_status()


@router.get("/cache", response_model=ModelCacheInfo)
async def get_model_cache() -> ModelCacheInfo:
    """Lists model weights already downloaded to this machine, with sizes.

    One cache serves every runtime, so this is a property of the machine rather than
    of any one server.
    """
    return await registry.get_cache_info()


@router.delete("/cache/{repo_id:path}")
async def delete_cached_model(repo_id: str) -> Dict[str, object]:
    """Deletes one repository's downloaded weights.

    Args:
        repo_id: Hugging Face repository id, e.g. ``Qwen/Qwen2.5-Coder-7B-Instruct``.

    Returns:
        The number of bytes reclaimed.
    """
    if registry.is_serving(repo_id):
        raise HTTPException(
            status_code=409,
            detail="This model is running. Stop it before deleting it.",
        )

    try:
        freed = await registry.delete_cached_model(repo_id)
    except CachePathError as error:
        logger.warning("Refused to delete %r from the model cache: %s", repo_id, error)
        raise HTTPException(
            status_code=400, detail="Kayak couldn't find this model's files on this Mac."
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="This model's files are already gone from this Mac.",
        )
    except OSError:
        logger.exception("Could not delete %s from the model cache", repo_id)
        raise HTTPException(
            status_code=500, detail="Kayak couldn't delete this model's files. Try again."
        )

    return {"status": "deleted", "repo_id": repo_id, "freed_bytes": freed}


# Modality-scoped routes are declared last on purpose: FastAPI matches in
# declaration order, and `/{modality}/status` would otherwise swallow the fixed
# routes above.
@router.get("/{modality}/status", response_model=DeploymentProgress)
async def get_status(modality: Modality) -> DeploymentProgress:
    """Fetches one server's lifecycle state, health, and logs tail."""
    return await _manager(modality).check_and_sync_status()


@router.post("/{modality}/deploy", response_model=DeploymentProgress)
async def deploy_model(modality: Modality, request: DeployRequest) -> DeploymentProgress:
    """Starts a container serving the requested model for one modality.

    Args:
        modality: Which server to start.
        request: Model identifier and resource configuration.

    Returns:
        Initial deployment state. The caller watches the event stream for the rest.
    """
    if not request.model_id.strip():
        raise HTTPException(status_code=400, detail="Choose a model to start.")

    manager = _manager(modality)

    if manager.runtime.supports_metal and metal.is_mlx_model(request.model_id):
        status = metal.read_status()
        if not status.supported:
            raise HTTPException(status_code=400, detail=metal_unavailable_message(status))

    return await manager.deploy_model(request)


@router.post("/{modality}/stop")
async def stop_server(modality: Modality) -> Dict[str, str]:
    """Stops and removes one modality's container or Metal server.

    Leaves every other server running: stopping speech synthesis must not take the
    text model down with it.
    """
    manager = _manager(modality)
    await manager.stop_server()
    return {
        "status": "stopped",
        "message": f"{manager.runtime.server_label} stopped.",
    }


@router.get("/events")
async def stream_events() -> StreamingResponse:
    """Live status and log events for every local server on one connection.

    Each frame names the modality it describes, so a client renders a voice model's
    startup and a text model's independently without a stream per server.
    """
    queue = registry.subscribe()

    async def frames():
        try:
            while True:
                data = await queue.get()
                yield sse.frame(data, event=data.get("type", "update"))
        except asyncio.CancelledError:
            pass
        finally:
            registry.unsubscribe(queue)

    return sse.response(frames())
