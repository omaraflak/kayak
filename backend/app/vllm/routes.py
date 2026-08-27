import asyncio
import json
import logging
from typing import Any, Dict, List
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from backend.app.vllm import metal
from backend.app.vllm.cache import CachePathError
from backend.app.vllm.manager import vllm_manager
from backend.app.vllm.models import (
    HostCapability,
    MetalStartRequest,
    MetalStatus,
    ModelCacheInfo,
    VLLMDeployRequest,
    VLLMDeploymentProgress,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/vllm", tags=["vllm"])


@router.get("/status", response_model=VLLMDeploymentProgress)
async def get_vllm_status() -> VLLMDeploymentProgress:
    """Fetches the current lifecycle state, health, and logs tail of the vLLM server.

    Returns:
        VLLMDeploymentProgress telemetry object.
    """
    return await vllm_manager.check_and_sync_status()


@router.post("/deploy", response_model=VLLMDeploymentProgress)
async def deploy_vllm_model(request: VLLMDeployRequest) -> VLLMDeploymentProgress:
    """Starts a Docker container or Metal process running vLLM for the requested model.

    Args:
        request: Model identifier and GPU/memory configuration.

    Returns:
        Initial deployment state.
    """
    if not request.model_id.strip():
        raise HTTPException(status_code=400, detail="Model ID is required.")

    if metal.is_mlx_model(request.model_id):
        status = metal.read_status()
        if not status.supported:
            raise HTTPException(
                status_code=400,
                detail="Metal inference needs the Kayak launcher running on an Apple Silicon Mac.",
            )

    return await vllm_manager.deploy_model(request)


@router.post("/stop")
async def stop_vllm_server() -> Dict[str, str]:
    """Stops and removes the active vLLM Docker container or Metal server.

    Returns:
        Status confirmation dictionary.
    """
    await vllm_manager.stop_server()
    return {"status": "stopped", "message": "vLLM server stopped."}


@router.get("/models")
async def get_vllm_served_models() -> List[Dict[str, Any]]:
    """Returns the list of active models served by the running vLLM server."""
    return await vllm_manager.list_served_models()


@router.get("/hardware", response_model=HostCapability)
async def get_host_capability() -> HostCapability:
    """Reports what this machine can serve: Docker, accelerators, and image state."""
    return await vllm_manager.get_host_capability()


@router.get("/metal", response_model=MetalStatus)
async def get_metal_status() -> MetalStatus:
    """Reports Metal inference as the desktop launcher currently sees it."""
    return metal.read_status()


@router.post("/metal/start", response_model=MetalStatus)
async def start_metal(request: MetalStartRequest) -> MetalStatus:
    """Asks the launcher to serve a model on the host GPU.

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
    await vllm_manager.deploy_model(VLLMDeployRequest(model_id=model_id))
    return metal.read_status()


@router.post("/metal/stop", response_model=MetalStatus)
async def stop_metal() -> MetalStatus:
    """Asks the launcher to stop the Metal server."""
    await vllm_manager.stop_server()
    return metal.read_status()


@router.get("/cache", response_model=ModelCacheInfo)
async def get_model_cache() -> ModelCacheInfo:
    """Lists model weights already downloaded to this machine, with sizes."""
    return await vllm_manager.get_cache_info()


@router.delete("/cache/{repo_id:path}")
async def delete_cached_model(repo_id: str) -> Dict[str, object]:
    """Deletes one repository's downloaded weights.

    Args:
        repo_id: Hugging Face repository id, e.g. ``Qwen/Qwen2.5-Coder-7B-Instruct``.

    Returns:
        The number of bytes reclaimed.
    """
    if vllm_manager.is_serving(repo_id):
        raise HTTPException(
            status_code=409,
            detail="This model is currently being served. Stop the server before deleting its weights.",
        )

    try:
        freed = await vllm_manager.delete_cached_model(repo_id)
    except CachePathError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error))
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"Could not delete weights: {error}")

    return {"status": "deleted", "repo_id": repo_id, "freed_bytes": freed}


@router.get("/events")
async def stream_vllm_events() -> StreamingResponse:
    """Server-Sent Events (SSE) endpoint providing real-time deployment status and streaming logs."""
    queue = vllm_manager.subscribe()

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
            vllm_manager.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
