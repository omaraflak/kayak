import asyncio
import json
import logging
from typing import Dict, List

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from backend.app.config import settings
from backend.app.inference import metal, registry
from backend.app.inference.cache import CachePathError
from backend.app.inference.models import (
    DeployRequest,
    DeploymentProgress,
    HostCapability,
    MetalStartRequest,
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
            detail=f"No local runtime serves '{modality.value}' models.",
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
    """Which local runtime should serve a repository.

    Asked before starting a model whose task is not already known -- a cached model
    is only a name and a size on disk. Answered from the Hub rather than guessed from
    the name: starting a cached Kokoro used to go to vLLM, which spent minutes on a
    model it could never load.
    """
    url = f"https://huggingface.co/api/models/{repo_id}"
    headers: Dict[str, str] = {}
    if settings.HUGGINGFACE_API_KEY:
        headers["Authorization"] = f"Bearer {settings.HUGGINGFACE_API_KEY}"

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError:
        # Unreachable is not the same as unsupported. The caller falls back rather
        # than refusing to start a model because the Hub was down.
        return ModelClassification(repo_id=repo_id, unknown=True)

    if response.status_code != 200:
        return ModelClassification(repo_id=repo_id, unknown=True)

    try:
        payload = response.json()
    except ValueError:
        return ModelClassification(repo_id=repo_id, unknown=True)

    return registry.classify(
        repo_id,
        pipeline_tag=payload.get("pipeline_tag"),
        library_name=payload.get("library_name"),
        tags=[tag for tag in (payload.get("tags") or []) if isinstance(tag, str)],
    )


@router.get("/status", response_model=Dict[str, DeploymentProgress])
async def get_all_status() -> Dict[str, DeploymentProgress]:
    """The current state of every local server, keyed by modality."""
    statuses = await asyncio.gather(
        *(manager.check_and_sync_status() for manager in registry.managers())
    )
    return {status.modality.value: status for status in statuses}


@router.get("/hardware", response_model=HostCapability)
async def get_host_capability() -> HostCapability:
    """Reports what this machine can serve: Docker, accelerators, and image state."""
    return await registry.text_manager.get_host_capability()


@router.get("/metal", response_model=MetalStatus)
async def get_metal_status() -> MetalStatus:
    """Reports Metal inference as the desktop launcher currently sees it."""
    return metal.read_status()


@router.post("/metal/start", response_model=MetalStatus)
async def start_metal(request: MetalStartRequest) -> MetalStatus:
    """Asks the launcher to serve a text model on the host GPU.

    Kayak cannot start this itself: Metal is unreachable from inside a
    container, so the request is recorded for the launcher to act on. The reply
    is the state at the moment of asking, not the result -- the caller polls
    ``GET /metal`` to watch it come up.
    """
    model_id = request.model_id.strip()
    status = metal.read_status()

    if not status.supported:
        logger.warning(
            "Metal start refused for %s: the launcher reports it unsupported (detail: %s)",
            model_id,
            status.detail or "none",
        )
        raise HTTPException(
            status_code=409,
            detail=(
                "Metal inference needs the Kayak launcher running on an Apple Silicon Mac."
            ),
        )
    if not metal.is_mlx_model(model_id):
        raise HTTPException(
            status_code=400,
            detail=(
                "Metal serves MLX models only. Choose a repository published under "
                "mlx-community."
            ),
        )

    logger.info("Asking the launcher to serve %s on the GPU", model_id)
    await registry.text_manager.deploy_model(DeployRequest(model_id=model_id))
    return metal.read_status()


@router.post("/metal/stop", response_model=MetalStatus)
async def stop_metal() -> MetalStatus:
    """Asks the launcher to stop the Metal server."""
    await registry.text_manager.stop_server()
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
            detail="This model is currently being served. Stop the server before deleting its weights.",
        )

    try:
        freed = await registry.delete_cached_model(repo_id)
    except CachePathError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error))
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"Could not delete weights: {error}")

    return {"status": "deleted", "repo_id": repo_id, "freed_bytes": freed}


# Modality-scoped routes are declared last on purpose. FastAPI matches in
# declaration order, and `/{modality}/stop` would otherwise swallow
# `/metal/stop` -- matching the shape, then rejecting "metal" as an unknown
# modality instead of ever reaching the Metal route.
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
        raise HTTPException(status_code=400, detail="Model ID is required.")

    manager = _manager(modality)

    if manager.runtime.supports_metal and metal.is_mlx_model(request.model_id):
        status = metal.read_status()
        if not status.supported:
            raise HTTPException(
                status_code=400,
                detail="Metal inference needs the Kayak launcher running on an Apple Silicon Mac.",
            )

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

    async def event_generator():
        try:
            while True:
                data = await queue.get()
                event_type = data.get("type", "update")
                payload = json.dumps(data)
                yield f"event: {event_type}\ndata: {payload}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            registry.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
