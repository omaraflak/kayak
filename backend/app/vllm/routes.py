import asyncio
import json
from typing import Any, Dict
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from backend.app.vllm.manager import vllm_manager
from backend.app.vllm.models import (
    VLLMDeployRequest,
    VLLMDeploymentProgress,
)

router = APIRouter(prefix="/api/vllm", tags=["vllm"])


@router.get("/status", response_model=VLLMDeploymentProgress)
async def get_vllm_status() -> VLLMDeploymentProgress:
    """Fetches the current lifecycle state, health, and logs tail of the vLLM server.

    Returns:
        VLLMDeploymentProgress telemetry object.
    """
    return vllm_manager.get_status()


@router.post("/deploy", response_model=VLLMDeploymentProgress)
async def deploy_vllm_model(request: VLLMDeployRequest) -> VLLMDeploymentProgress:
    """Starts a Docker container running vLLM for the requested model and streams download progress.

    Args:
        request: Model identifier and GPU/memory configuration.

    Returns:
        Initial deployment state.
    """
    if not request.model_id.strip():
        raise HTTPException(status_code=400, detail="Model ID is required.")

    return await vllm_manager.deploy_model(request)


@router.post("/stop")
async def stop_vllm_server() -> Dict[str, str]:
    """Stops and removes the active vLLM Docker container.

    Returns:
        Status confirmation dictionary.
    """
    await vllm_manager.stop_server()
    return {"status": "stopped", "message": "vLLM server container stopped."}


@router.get("/events")
async def stream_vllm_events():
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
