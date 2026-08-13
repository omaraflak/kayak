"""Cheap staleness detection for disk-backed registries.

Agents and skills live as files that a user or an agent may edit at any moment, so
the registries must notice outside changes. They previously did that by re-reading
and re-parsing every file on every lookup, which runs several times per agent
iteration. Comparing a directory fingerprint instead keeps edits visible while
reducing the common case to a handful of stat calls.
"""

from pathlib import Path
from typing import Iterable, Tuple

DirectorySignature = Tuple[Tuple[str, int, int], ...]


def directory_signature(
    root: Path,
    patterns: Iterable[str] = ("*",),
    recursive: bool = False,
) -> DirectorySignature:
    """Builds a fingerprint of a directory's contents from paths, sizes, and mtimes.

    Args:
        root: Directory to fingerprint.
        patterns: Glob patterns to include.
        recursive: Whether to descend into subdirectories.

    Returns:
        DirectorySignature: A comparable, order-stable fingerprint. An empty tuple
        is returned for a missing directory.
    """
    if not root.exists():
        return ()

    entries: list[Tuple[str, int, int]] = []
    for pattern in patterns:
        matches = root.rglob(pattern) if recursive else root.glob(pattern)
        for path in matches:
            try:
                stat = path.stat()
            except OSError:
                continue
            entries.append((str(path), stat.st_size, stat.st_mtime_ns))

    return tuple(sorted(set(entries)))
