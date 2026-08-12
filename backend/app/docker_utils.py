import os
import socket
from pathlib import Path
from typing import Optional, Dict, Any, Union
import docker

class DockerPathResolver:
    """Resolves container-internal filesystem paths to Docker host paths or named volumes.

    This enables seamless Docker-outside-of-Docker (DooD) container spawning across macOS,
    Linux, and Windows without file-sharing permission errors.
    """

    _initialized: bool = False
    _host_mount_map: Dict[str, str] = {}
    _is_in_container: bool = False

    @classmethod
    def initialize(cls, client: Optional[docker.DockerClient] = None):
        """Initializes host path mapping from environment variables or container inspection."""
        if cls._initialized:
            return
        cls._initialized = True

        cls._is_in_container = os.path.exists("/.dockerenv") or os.environ.get("KAYAK_IN_DOCKER") == "true"

        # 1. Check explicit host data directory override from environment / docker-compose
        host_data_dir = os.environ.get("KAYAK_HOST_DATA_DIR")
        if host_data_dir:
            cls._host_mount_map["/app/data"] = host_data_dir.rstrip("/")

        if not client or not cls._is_in_container:
            return

        # 2. Inspect running container mounts via Docker daemon to auto-discover host paths
        try:
            candidates = [socket.gethostname(), "kayak-server"]
            target_container = None
            for cid in candidates:
                try:
                    target_container = client.containers.get(cid)
                    if target_container:
                        break
                except Exception:
                    continue

            if target_container:
                mounts = target_container.attrs.get("Mounts", [])
                for mount in mounts:
                    dest = mount.get("Destination")
                    src = mount.get("Source")
                    m_type = mount.get("Type")
                    name = mount.get("Name")

                    if dest:
                        dest_clean = dest.rstrip("/")
                        if m_type == "volume" and name:
                            cls._host_mount_map[dest_clean] = name
                        elif src:
                            cls._host_mount_map[dest_clean] = src.rstrip("/")
        except Exception:
            pass

    @classmethod
    def is_in_container(cls) -> bool:
        return cls._is_in_container

    @classmethod
    def resolve_volume_source(cls, container_path: Union[str, Path], fallback_named_volume: Optional[str] = None) -> str:
        """Translates an internal container path (e.g. /app/data/huggingface_cache) to the host source path.

        Args:
            container_path: Path on the current filesystem.
            fallback_named_volume: Optional named volume to return if running in Docker and host path cannot be resolved.

        Returns:
            Resolved host path or volume name for Docker daemon bind mounts.
        """
        path_obj = Path(container_path).resolve()
        path_str = str(path_obj).rstrip("/")

        # Check known container-to-host mount mappings
        for dest, src in sorted(cls._host_mount_map.items(), key=lambda x: len(x[0]), reverse=True):
            if path_str == dest:
                return src
            if path_str.startswith(dest + "/"):
                rel = os.path.relpath(path_str, dest)
                # If src is an absolute host path (Linux/macOS/Windows)
                if src.startswith("/") or src.startswith("\\") or (len(src) > 1 and src[1] == ":"):
                    return os.path.normpath(os.path.join(src, rel))
                else:
                    # Named volume
                    return src

        # If inside a container and path starts with /app/ but cannot be mapped to host
        if cls._is_in_container and path_str.startswith("/app"):
            if fallback_named_volume:
                return fallback_named_volume

        return path_str
