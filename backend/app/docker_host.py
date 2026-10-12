"""The Docker daemon Kayak drives: Docker Desktop on the user's Mac.

Kayak starts sandboxes and model servers as sibling containers on that daemon and
bind-mounts directories into them. The daemon resolves a bind-mount source on the
Mac, not inside Kayak's own container, so a path under `/app/data` has to be
translated to the Mac directory mounted there before Docker can use it.
"""

from functools import lru_cache
import os
from pathlib import Path
import socket
from typing import Any, Dict, List, Optional, Union

import docker

from backend.app.config import settings

#: Container path -> Mac path, for every bind mount into Kayak's own container.
#: Computed once: the mounts of a running container cannot change.
_mounts: Optional[Dict[str, str]] = None


def connect() -> Optional[docker.DockerClient]:
    """Opens a client to the daemon, or returns None while it is not answering."""
    try:
        client = docker.from_env()
        client.ping()
        return client
    except Exception:
        return None


def _own_container() -> Optional[Any]:
    """The container this process runs in, when it runs in one and Docker answers."""
    if not settings.RUNNING_IN_CONTAINER:
        return None
    client = connect()
    if client is None:
        return None
    try:
        return client.containers.get(socket.gethostname())
    except Exception:
        return None


@lru_cache(maxsize=1)
def published_hosts(port: int) -> Optional[List[str]]:
    """The Mac addresses a containerised Kayak's port is published on.

    Binding 0.0.0.0 inside the container says nothing about who can reach it: the
    launcher and docker-compose publish the port on 127.0.0.1 only. An empty host
    means every interface. None when this cannot be read, including when Kayak
    runs on the Mac directly.
    """
    own = _own_container()
    if own is None:
        return None
    bindings = (own.attrs.get("HostConfig", {}).get("PortBindings") or {}).get(f"{port}/tcp")
    return [binding.get("HostIp") or "0.0.0.0" for binding in bindings or []]


def _discover_mounts() -> Dict[str, str]:
    """Finds where this process's data directory lives on the Mac.

    The launcher and docker-compose both say so explicitly through
    KAYAK_HOST_DATA_DIR. A container started some other way is asked about its
    own bind mounts instead.
    """
    declared = os.environ.get("KAYAK_HOST_DATA_DIR")
    if declared:
        return {str(settings.DATA_DIR): declared.rstrip("/")}

    own = _own_container()
    if own is None:
        return {}
    return {
        mount["Destination"].rstrip("/"): mount["Source"].rstrip("/")
        for mount in own.attrs.get("Mounts", [])
        if mount.get("Type") == "bind" and mount.get("Destination") and mount.get("Source")
    }


def host_path(path: Union[str, Path]) -> str:
    """Translates a path in this process into the Mac path Docker can bind-mount.

    A path outside every known mount is returned unchanged, which is correct
    when Kayak runs directly on the Mac rather than in a container.
    """
    global _mounts
    if _mounts is None:
        _mounts = _discover_mounts()

    resolved = str(Path(path).resolve()).rstrip("/")
    # Longest mount first, so a nested mount wins over the one containing it.
    for inside, outside in sorted(_mounts.items(), key=lambda item: len(item[0]), reverse=True):
        if resolved == inside:
            return outside
        if resolved.startswith(inside + "/"):
            return outside + resolved[len(inside):]
    return resolved
