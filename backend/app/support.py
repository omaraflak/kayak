"""Assembles everything needed to diagnose a problem from a user's machine.

Kayak spans two processes on opposite sides of a container boundary: the server
here, and the desktop launcher on the host. Neither can see the other's output,
and a user reporting "it didn't work" has access to neither. This gathers both
into one document they can send.

Log records are kept in memory rather than read back from a file because the
server logs to stdout, which inside a container is owned by Docker and not
readable from within.
"""

from collections import deque
import json
import logging
import platform
from typing import Deque, List, Optional

from backend.app.config import settings
from backend.app.vllm import metal

#: Records retained. Enough to cover a failed startup and the conversation that
#: preceded it, without keeping the whole session in memory.
CAPACITY = 2000

#: Where the launcher mirrors its own records, inside the shared data directory.
LAUNCHER_LOG = "launcher.log"

#: Lines of the launcher's log to include. Its records are host-side and cover
#: the part of a failure Kayak never sees.
LAUNCHER_LOG_LINES = 400


class MemoryLogHandler(logging.Handler):
    """Keeps the most recent log records so they can be dumped on request."""

    def __init__(self, capacity: int = CAPACITY):
        super().__init__()
        self.records: Deque[str] = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.records.append(self.format(record))
        except Exception:
            # A logging handler that raises would take down whatever was being
            # logged, which is never worth it for diagnostics.
            pass


_handler: Optional[MemoryLogHandler] = None


def install() -> MemoryLogHandler:
    """Attaches the in-memory handler to the root logger, once."""
    global _handler
    if _handler is None:
        _handler = MemoryLogHandler()
        _handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        logging.getLogger().addHandler(_handler)
    return _handler


def recent_logs() -> List[str]:
    return list(_handler.records) if _handler else []


def _launcher_log() -> List[str]:
    """Reads the tail of the launcher's log, when a launcher is running."""
    path = metal.control_dir() / LAUNCHER_LOG
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return lines[-LAUNCHER_LOG_LINES:]


def versions() -> dict[str, Optional[str]]:
    """Reports the versions of both installed pieces.

    Kayak's own version is baked in at build time; the launcher's arrives
    through the control file, since it is a separate program.
    """
    launcher: Optional[str] = None
    try:
        raw = metal.status_path().read_text(encoding="utf-8")
        launcher = (json.loads(raw).get("versions") or {}).get("launcher")
    except (OSError, json.JSONDecodeError, AttributeError):
        pass

    return {"kayak": settings.VERSION, "launcher": launcher}


def _docker_environment() -> str:
    """Reports how Docker is configured, as the container can see it.

    A registry mirror or a proxy configured on the daemon can serve a stale
    image for a tag that has already moved, which looks from the outside exactly
    like the app refusing to update. It is invisible from anywhere else in
    Kayak, so it is worth stating here.
    """
    try:
        import docker as docker_sdk

        info = docker_sdk.from_env().info()
    except Exception as error:  # noqa: BLE001 - any failure is just "unavailable"
        return f"unavailable ({error})"

    mirrors = (info.get("RegistryConfig") or {}).get("Mirrors") or []
    return "\n".join(
        [
            f"server version:   {info.get('ServerVersion')}",
            f"os/arch:          {info.get('OperatingSystem')} {info.get('Architecture')}",
            f"registry mirrors: {', '.join(mirrors) if mirrors else 'none'}",
            f"http proxy:       {info.get('HttpProxy') or 'none'}",
        ]
    )


def _section(title: str, body: str) -> str:
    return f"\n===== {title} =====\n{body.strip() or '(nothing)'}\n"


def build_bundle() -> str:
    """Builds the support document, as plain text.

    Plain text on purpose: it has to survive being pasted into an email body or
    attached as a file, and be readable without any tooling.
    """
    installed = versions()
    metal_status = metal.read_status()

    header = "\n".join(
        [
            f"Kayak version:    {installed['kayak']}",
            f"Launcher version: {installed['launcher'] or 'not running'}",
            f"Platform:         {platform.platform()}",
            f"Python:           {platform.python_version()}",
            f"Data directory:   {settings.DATA_DIR}",
        ]
    )

    metal_lines = "\n".join(
        [
            f"supported: {metal_status.supported}",
            f"installed: {metal_status.installed}",
            f"state:     {metal_status.state}",
            f"model:     {metal_status.model or '-'}",
            f"error:     {metal_status.error or '-'}",
            f"detail:    {metal_status.detail or '-'}",
        ]
    )

    return "".join(
        [
            "Kayak support bundle",
            "\n",
            _section("Versions", header),
            _section("GPU inference", metal_lines),
            _section("Docker", _docker_environment()),
            _section("Launcher log", "\n".join(_launcher_log())),
            _section("Kayak server log", "\n".join(recent_logs())),
        ]
    )
