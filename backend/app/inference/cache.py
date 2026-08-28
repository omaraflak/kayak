"""Inventory of model weights downloaded to this machine.

Serving models locally costs disk before it costs anything else -- a single 7B model is
roughly 15 GB, and the cache directory is mounted into the container where nothing in
the app ever looks at it again. Without an inventory the only way to find out what a
machine is holding is `du`, and the only way to reclaim it is `rm -rf`.
"""

import os
import re
from pathlib import Path
from typing import List, Optional

from backend.app.inference.models import CachedModel

# Hugging Face stores each repository as `<cache>/hub/models--Org--Name`, with real file
# content under `blobs/` and `snapshots/<revision>/` holding symlinks into it.
HUB_SUBDIRECTORY = "hub"
REPO_DIR_PREFIX = "models--"
_SAFE_REPO_ID = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")


class CachePathError(ValueError):
    """Raised when a repository id does not name a directory inside the cache."""


def repo_dir_to_repo_id(directory_name: str) -> Optional[str]:
    """Converts a cache directory name back to its Hugging Face repository id.

    Args:
        directory_name: A directory name such as ``models--Qwen--Qwen2.5-Coder-7B``.

    Returns:
        Optional[str]: The repository id, or None if the name is not a cache entry.
    """
    if not directory_name.startswith(REPO_DIR_PREFIX):
        return None
    remainder = directory_name[len(REPO_DIR_PREFIX):]
    if not remainder:
        return None
    return remainder.replace("--", "/")


def repo_id_to_repo_dir(repo_id: str) -> str:
    """Converts a Hugging Face repository id to its cache directory name."""
    return REPO_DIR_PREFIX + repo_id.replace("/", "--")


def directory_size_bytes(path: Path) -> int:
    """Sums the size of real files under a directory.

    Symlinks are skipped: the snapshot tree links into ``blobs/``, so following them
    would report every file twice.
    """
    total = 0
    for root, _dirs, files in os.walk(path, followlinks=False):
        for name in files:
            entry = Path(root) / name
            try:
                stat = entry.lstat()
            except OSError:
                continue
            if os.path.islink(entry):
                continue
            total += stat.st_size
    return total


def resolve_cache_entry(cache_root: Path, repo_id: str) -> Path:
    """Resolves a repository id to its directory, refusing anything outside the cache.

    Args:
        cache_root: The Hugging Face cache directory mounted into the container.
        repo_id: Repository id supplied by the caller.

    Returns:
        Path: The resolved repository directory.

    Raises:
        CachePathError: If the id is malformed or resolves outside the cache root.
    """
    if not repo_id or not _SAFE_REPO_ID.match(repo_id):
        raise CachePathError(f"'{repo_id}' is not a valid repository id.")

    # A traversal segment survives the character class, and only the later '/' -> '--'
    # substitution keeps it from escaping. Rejecting it outright means the guard does
    # not rest on that coincidence.
    if any(segment in (".", "..") for segment in repo_id.split("/")):
        raise CachePathError(f"'{repo_id}' is not a valid repository id.")

    hub_root = (cache_root / HUB_SUBDIRECTORY).resolve()
    candidate = (hub_root / repo_id_to_repo_dir(repo_id)).resolve()

    if candidate != hub_root and hub_root not in candidate.parents:
        raise CachePathError(f"'{repo_id}' resolves outside the model cache.")

    return candidate


def list_cached_models(cache_root: Path) -> List[CachedModel]:
    """Lists model repositories present in the local Hugging Face cache.

    Args:
        cache_root: The Hugging Face cache directory.

    Returns:
        List[CachedModel]: Cached repositories, largest first.
    """
    hub_root = cache_root / HUB_SUBDIRECTORY
    if not hub_root.is_dir():
        return []

    models: List[CachedModel] = []
    for entry in hub_root.iterdir():
        if not entry.is_dir():
            continue
        repo_id = repo_dir_to_repo_id(entry.name)
        if not repo_id:
            continue
        try:
            modified_at = entry.stat().st_mtime
        except OSError:
            modified_at = 0.0
        models.append(
            CachedModel(
                repo_id=repo_id,
                size_bytes=directory_size_bytes(entry),
                modified_at=modified_at,
            )
        )

    return sorted(models, key=lambda model: model.size_bytes, reverse=True)


