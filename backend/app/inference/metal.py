"""Metal inference, driven through the launcher.

Docker Desktop has no GPU passthrough, so a Metal server cannot be a container
the way the CPU deployments are. It has to be a native macOS process, and the
only part of Kayak running on the Mac itself is the desktop launcher. Kayak
therefore does not start anything here: it writes the state it wants into the
data directory it already shares with the launcher, and reads back what the
launcher made true.

The exchange is deliberately declarative rather than a request/response call.
Neither side can assume the other is running -- Kayak works with no launcher at
all, and the launcher restarts independently -- so writing a desired state that
is reconciled converges from any starting point, where a missed command would
not.
"""

from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Optional

from backend.app.config import settings
from backend.app.inference.models import MetalStatus

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

#: Said when the launcher's status file has gone stale. The launcher is part of
#: Kayak as far as anyone using it knows, so it is not named.
LAUNCHER_STOPPED_REPORTING = (
    "Part of Kayak stopped responding, so models can't use the GPU right now. Quit "
    "Kayak and open it again."
)


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
        return MetalStatus(detail=LAUNCHER_STOPPED_REPORTING)

    try:
        payload = json.loads(raw).get("metal", {})
    except (json.JSONDecodeError, AttributeError):
        # A half-written file is transient; the launcher rewrites it every few
        # seconds, so this is not worth surfacing as an error.
        return MetalStatus()

    if not isinstance(payload, dict):
        return MetalStatus()

    # Read field by field, tolerating anything: a launcher from another release
    # may add fields, drop them, or write a type this build does not expect, and
    # none of that may stop Kayak from reading the parts it understands.
    return MetalStatus(
        supported=bool(payload.get("supported", False)),
        installed=bool(payload.get("installed", False)),
        state=_text(payload.get("state")) or "stopped",
        model=_text(payload.get("model")),
        port=_port(payload.get("port")),
        error=_text(payload.get("error")),
        error_detail=_text(payload.get("error_detail")),
        detail=_text(payload.get("detail")),
        request=_text(payload.get("request")),
        # Key presence, not value: a launcher new enough to echo requests
        # writes the key on every status, null included. The same holds for
        # error_detail, whose presence means `error` is a plain sentence.
        acknowledges_requests="request" in payload,
        separates_error_detail="error_detail" in payload,
    )


def _text(value: Any) -> Optional[str]:
    """A string field, or None for anything missing, empty or not a string."""
    return value if isinstance(value, str) and value.strip() else None


def _port(value: Any) -> int:
    """A port number, or 0 when the launcher did not report a usable one."""
    try:
        port = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return port if 0 < port < 65536 else 0


@dataclass(frozen=True)
class MetalDesired:
    """What Kayak last asked the launcher for, read back from desired.json.

    Kept because the launcher reconciles on a delay: for a couple of seconds after
    a stop, its status file still says "ready", and only comparing it against what
    was actually asked for tells a server being wound down from one that is wanted.
    """
    running: bool = False
    model: Optional[str] = None
    request: Optional[str] = None


def read_desired() -> MetalDesired:
    """Reads the state Kayak last asked the launcher for.

    Anything unreadable is "nothing wanted": the file is only ever written whole,
    so a missing or broken one means no start has been asked for since it was
    lost, and treating that as "keep running" is how a stopped model came back.
    """
    try:
        payload = json.loads(desired_path().read_text(encoding="utf-8")).get("metal", {})
    except (OSError, ValueError, AttributeError):
        return MetalDesired()
    if not isinstance(payload, dict):
        return MetalDesired()
    return MetalDesired(
        running=payload.get("running") is True,
        model=_text(payload.get("model")),
        request=_text(payload.get("request")),
    )


def write_desired(
    running: bool,
    model: Optional[str] = None,
    request: Optional[str] = None,
    hf_token: Optional[str] = None,
) -> None:
    """Records the state the launcher should bring about.

    The ``request`` token identifies this particular ask; the launcher echoes
    it in every status it writes afterwards, which is what lets the caller
    tell "ready, answering your request" from a stale "ready" describing the
    server that ran before the request was made.

    ``hf_token`` is the user's Hugging Face token, which the launcher needs to
    download a model whose makers ask for one. It is only written with a start:
    a stop has nothing to download, and the token should not sit on disk longer
    than it is needed. The file is readable by its owner alone for the same
    reason.

    Written atomically because the launcher polls this file continuously and
    must never read a half-written document as "stop everything".

    Raises:
        OSError: If the file cannot be written -- a full disk, or a data
            directory that is not writable. Callers report it rather than leave
            a start or a stop half done.
    """
    directory = control_dir()
    directory.mkdir(parents=True, exist_ok=True)
    metal: dict = {"running": running, "model": model, "request": request}
    if running and hf_token:
        metal["hf_token"] = hf_token
    payload = {"metal": metal}

    handle, temp_name = tempfile.mkstemp(dir=str(directory), prefix=f".{DESIRED_FILENAME}.")
    temp_path = Path(temp_name)
    try:
        # mkstemp already creates the file for its owner only; set explicitly so
        # the guarantee does not rest on a default.
        os.fchmod(handle, 0o600)
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
