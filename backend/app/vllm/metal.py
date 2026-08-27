"""Metal inference, driven through the launcher.

Metal has no passthrough into Docker, so a Metal server cannot be a container
the way the CUDA and CPU deployments are. It has to be a native macOS process,
and the only part of Kayak running on the host is the desktop launcher. Kayak
therefore does not start anything here: it writes the state it wants into the
data directory it already shares with the launcher, and reads back what the
launcher made true.

The exchange is deliberately declarative rather than a request/response call.
Neither side can assume the other is running -- Kayak works with no launcher at
all, and the launcher restarts independently -- so writing a desired state that
is reconciled converges from any starting point, where a missed command would
not.
"""

import json
import logging
import os
from pathlib import Path
import tempfile
import time
from typing import Optional

from backend.app.config import settings
from backend.app.vllm.models import MetalStatus

logger = logging.getLogger(__name__)

#: Shared with the launcher, which watches this directory.
CONTROL_DIRNAME = ".launcher"
DESIRED_FILENAME = "desired.json"
STATUS_FILENAME = "status.json"

#: A running launcher rewrites the status file every couple of seconds, so a
#: file untouched for this long means no launcher is watching. Trusting it
#: anyway is how a "ready" written before the launcher quit kept a model
#: looking served — with a send button — when nothing was listening at all.
STALE_AFTER_SECONDS = 15


def control_dir() -> Path:
    return settings.DATA_DIR / CONTROL_DIRNAME


def status_path() -> Path:
    return control_dir() / STATUS_FILENAME


def desired_path() -> Path:
    return control_dir() / DESIRED_FILENAME


def read_status() -> MetalStatus:
    """Reports what the launcher says about Metal.

    An absent or unreadable file means no launcher is running -- Kayak started
    from docker-compose, or an older launcher without Metal support -- which is
    reported as unsupported so the UI hides an option that cannot work.
    """
    path = status_path()
    try:
        raw = path.read_text(encoding="utf-8")
        age = time.time() - path.stat().st_mtime
    except OSError:
        # Absent is the normal case without a launcher, so this is not logged;
        # it would be one line every poll.
        return MetalStatus()

    if age > STALE_AFTER_SECONDS:
        # The launcher that wrote this is no longer running (or no longer
        # responding), so nothing in the file can be acted on. Reported as
        # unsupported — the same as no launcher at all — with the reason.
        return MetalStatus(
            detail=(
                "The Kayak desktop app has stopped reporting. Make sure it is "
                "running to use the GPU."
            )
        )

    try:
        payload = json.loads(raw).get("metal", {})
    except (json.JSONDecodeError, AttributeError):
        # A half-written file is transient; the launcher rewrites it every few
        # seconds, so this is not worth surfacing as an error.
        return MetalStatus()

    if not isinstance(payload, dict):
        return MetalStatus()

    return MetalStatus(
        supported=bool(payload.get("supported", False)),
        installed=bool(payload.get("installed", False)),
        state=str(payload.get("state", "stopped")),
        model=payload.get("model"),
        port=int(payload.get("port") or 0),
        error=payload.get("error"),
        detail=payload.get("detail"),
        request=payload.get("request"),
        # Key presence, not value: a launcher new enough to echo requests
        # writes the key on every status, null included.
        acknowledges_requests="request" in payload,
    )


def write_desired(
    running: bool, model: Optional[str] = None, request: Optional[str] = None
) -> None:
    """Records the state the launcher should bring about.

    The ``request`` token identifies this particular ask; the launcher echoes
    it in every status it writes afterwards, which is what lets the caller
    tell "ready, answering your request" from a stale "ready" describing the
    server that ran before the request was made.

    Written atomically because the launcher polls this file continuously and
    must never read a half-written document as "stop everything".
    """
    directory = control_dir()
    directory.mkdir(parents=True, exist_ok=True)
    payload = {"metal": {"running": running, "model": model, "request": request}}

    handle, temp_name = tempfile.mkstemp(dir=str(directory), prefix=f".{DESIRED_FILENAME}.")
    temp_path = Path(temp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, desired_path())
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def is_mlx_model(model_id: str) -> bool:
    """Reports whether a repository is one the Metal backend can serve.

    vllm-metal runs MLX weights, published on Hugging Face under the
    ``mlx-community`` organisation. An ordinary repository fails minutes into a
    download, so it is refused before anything is requested.
    """
    parts = model_id.split("/")
    if len(parts) != 2:
        return False
    org, name = parts
    return org.lower() == "mlx-community" and bool(name.strip())
