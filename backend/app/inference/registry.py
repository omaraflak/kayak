"""The local servers this machine can run, one per modality.

There used to be a single manager, and therefore a single container: starting any
model stopped whatever was running. That is wrong as soon as more than one kind of
model exists, because a voice model is only useful *alongside* a text model -- an
agent that writes an answer and then speaks it needs both at once.

So there is one manager per modality, each with its own container, port and lifecycle,
and they share exactly two things: the Hugging Face weight cache on disk, and one
event stream. Everything else is independent, including failure: a speech server that
will not start cannot disturb a text server that is happily running.
"""

import asyncio
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.app.config import settings
from backend.app.inference import cache as model_cache
from backend.app.inference.manager import ServerManager, StatusBroadcaster
from backend.app.inference.models import (
    CachedModel,
    Modality,
    ModelCacheInfo,
    ModelClassification,
    RuntimeDescriptor,
)
from backend.app.inference.audio_runtimes import SpeechRuntime, TranscriptionRuntime
from backend.app.inference.runtimes import Runtime
from backend.app.inference.vllm_runtime import VLLMRuntime

#: One stream carries every modality. Browsers cap concurrent HTTP/1.1 connections at
#: six per origin, a ceiling the app has hit before, so a connection per server is not
#: affordable.
_broadcaster = StatusBroadcaster()

_RUNTIMES: tuple = (VLLMRuntime(), SpeechRuntime(), TranscriptionRuntime())

_MANAGERS: Dict[Modality, ServerManager] = {
    runtime.modality: ServerManager(runtime, _broadcaster) for runtime in _RUNTIMES
}

#: The text manager by name, for the many callers that mean "the LLM server".
text_manager: ServerManager = _MANAGERS[Modality.TEXT]


def manager_for(modality: Modality) -> ServerManager:
    """The manager serving one modality.

    Raises:
        KeyError: If no runtime serves that modality.
    """
    return _MANAGERS[modality]


def managers() -> List[ServerManager]:
    """Every manager, in declaration order."""
    return list(_MANAGERS.values())


def runtimes() -> List[Runtime]:
    """Every runtime, in declaration order."""
    return [manager.runtime for manager in _MANAGERS.values()]


def describe_runtimes() -> List[RuntimeDescriptor]:
    """What clients need to know about each runtime, served rather than hardcoded."""
    return [runtime.describe() for runtime in runtimes()]


def classify(
    repo_id: str,
    pipeline_tag: Optional[str] = None,
    library_name: Optional[str] = None,
    tags: Optional[List[str]] = None,
) -> ModelClassification:
    """Which runtime should serve a repository, from Hub metadata.

    The task decides the runtime, and the runtime then says whether it can actually
    load that repository. Both halves matter: a text-to-speech model belongs to the
    speech runtime even when no backend can load it, and saying so is more useful
    than silently offering it to vLLM.
    """
    result = ModelClassification(
        repo_id=repo_id,
        pipeline_tag=pipeline_tag,
        library_name=library_name,
    )

    for runtime in runtimes():
        if pipeline_tag and pipeline_tag in runtime.pipeline_tags:
            result.modality = runtime.modality
            result.runtime_key = runtime.key
            result.supported = runtime.can_serve(repo_id, library_name, tags)
            return result

    # No runtime claims this task at all -- an image model, say. Reported as
    # unsupported with no modality rather than guessed into the text runtime.
    return result


def subscribe() -> asyncio.Queue:
    """Subscribes to live events from every server, greeted with each one's status."""
    return _broadcaster.subscribe(
        [manager.status_event() for manager in managers()]
    )


def unsubscribe(queue: asyncio.Queue) -> None:
    _broadcaster.unsubscribe(queue)


def is_serving(repo_id: str) -> bool:
    """Whether any server is currently serving this repository.

    Consulted before deleting weights. Checking one manager would have been enough
    when there was one; with several, deleting the weights out from under a running
    speech model because only the text server was checked is exactly the kind of
    failure this prevents.
    """
    return any(manager.is_serving(repo_id) for manager in managers())


def start_watchdogs() -> None:
    """Starts each manager's reconcile loop."""
    for manager in managers():
        manager.start_watchdog()


async def sync_all() -> None:
    """Reconciles every server against reality, concurrently.

    Run at startup so that containers surviving a backend restart are re-adopted.
    """
    await asyncio.gather(
        *(manager.check_and_sync_status() for manager in managers()),
        return_exceptions=True,
    )


async def shutdown() -> None:
    """Stops every manager's background monitors."""
    await asyncio.gather(
        *(manager.shutdown() for manager in managers()),
        return_exceptions=True,
    )


# --- The weight cache, shared by every runtime ----------------------------------
#
# All runtimes mount the same Hugging Face cache directory, so its size, contents and
# deletions are a property of the machine rather than of any one server.


def cache_root() -> Path:
    """Host directory mounted into every server container as the HF cache."""
    return settings.DATA_DIR / "huggingface_cache"


async def get_cache_info() -> ModelCacheInfo:
    """Lists locally downloaded model weights and their size on disk."""
    root = cache_root()
    loop = asyncio.get_running_loop()
    # Walking a weight cache means stat-ing tens of thousands of files.
    models: List[CachedModel] = await loop.run_in_executor(
        None, model_cache.list_cached_models, root
    )
    return ModelCacheInfo(
        path=str(root),
        total_bytes=sum(model.size_bytes for model in models),
        models=models,
    )


async def delete_cached_model(repo_id: str) -> int:
    """Removes a repository's weights from the local cache.

    Args:
        repo_id: Hugging Face repository id to evict.

    Returns:
        int: Bytes reclaimed.

    Raises:
        CachePathError: If the id does not name a directory inside the cache.
        FileNotFoundError: If the repository is not cached.
    """
    target = model_cache.resolve_cache_entry(cache_root(), repo_id)
    if not target.is_dir():
        raise FileNotFoundError(f"'{repo_id}' is not in the local cache.")

    def _remove() -> int:
        freed = model_cache.directory_size_bytes(target)
        shutil.rmtree(target)
        return freed

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _remove)
