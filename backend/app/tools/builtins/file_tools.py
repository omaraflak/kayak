"""Workspace file operations, confined to the conversation's workspace root.

Every path an agent supplies is untrusted: it can arrive from the model's own
reasoning or, indirectly, from content the agent fetched off the web. Both the host
and sandbox implementations therefore resolve symlinks and reject anything landing
outside the workspace, rather than trusting a relative-looking path to stay put.
"""

from pathlib import Path
from typing import List, Optional
from backend.app.agent.sandbox import sandbox_manager

# Hard cap on returned file content, independent of the line cap: a minified bundle
# can be a handful of lines and still exhaust the model's context window.
MAX_FILE_CHARS = 100_000
MAX_FILE_LINES = 500
MAX_DIR_ENTRIES = 1000

SANDBOX_WORKSPACE_ROOT = "/workspace"


class PathOutsideWorkspaceError(ValueError):
    """Raised when a requested path resolves outside the workspace root."""


def resolve_workspace_path(path_str: str, workspace_dir: Optional[Path]) -> Path:
    """Resolves a path against the workspace root and refuses to leave it.

    Args:
        path_str: Relative or absolute path supplied by the agent.
        workspace_dir: Root the conversation is confined to.

    Returns:
        Path: The fully resolved path, guaranteed to sit inside the workspace.

    Raises:
        PathOutsideWorkspaceError: If the path escapes the workspace root.
    """
    base = (workspace_dir if workspace_dir else Path.cwd()).resolve()
    target = Path(path_str)
    if not target.is_absolute():
        target = base / target

    # resolve() collapses '..' and follows symlinks, so a link planted inside the
    # workspace cannot be used as a way out of it.
    resolved = target.resolve()

    if resolved != base and base not in resolved.parents:
        raise PathOutsideWorkspaceError(
            f"Error: Path '{path_str}' resolves outside the workspace root"
            f" '{base}'. Access denied."
        )
    return resolved


# Mirrors resolve_workspace_path() for code executed inside the sandbox container,
# where the workspace is always mounted at /workspace.
_SANDBOX_PRELUDE = f"""
import sys
from pathlib import Path

WORKSPACE_ROOT = Path({SANDBOX_WORKSPACE_ROOT!r}).resolve()


def _confine(path_str):
    target = Path(path_str)
    if not target.is_absolute():
        target = WORKSPACE_ROOT / target
    resolved = target.resolve()
    if resolved != WORKSPACE_ROOT and WORKSPACE_ROOT not in resolved.parents:
        print(f"Error: Path '{{path_str}}' resolves outside the workspace root. Access denied.")
        sys.exit(0)
    return resolved
"""


def _format_file_lines(
    lines: List[str],
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
) -> str:
    """Formats file lines with 1-indexed line numbers, slicing if requested or capping at 500 lines."""
    if start_line is not None or end_line is not None:
        s = max(1, start_line) if start_line else 1
        e = min(len(lines), end_line) if end_line else len(lines)
        sliced_lines = lines[s - 1 : e]
        body = "".join([f"{i + s:4d} | {line}" for i, line in enumerate(sliced_lines)])
    elif len(lines) <= MAX_FILE_LINES:
        body = "".join([f"{i + 1:4d} | {line}" for i, line in enumerate(lines)])
    else:
        prefix = f"File has {len(lines)} lines. Showing first {MAX_FILE_LINES} lines:\n"
        body = prefix + "".join(
            [f"{i + 1:4d} | {line}" for i, line in enumerate(lines[:MAX_FILE_LINES])]
        )

    if len(body) > MAX_FILE_CHARS:
        body = (
            body[:MAX_FILE_CHARS]
            + f"\n... [truncated at {MAX_FILE_CHARS} characters; "
            "read a specific line range to see more]"
        )
    return body


def _format_dir_entries(items: List[Path], path_str: str) -> str:
    """Formats directory contents into a human-readable list."""
    if not items:
        return f"Directory '{path_str}' is empty."

    truncated = len(items) > MAX_DIR_ENTRIES
    output = []
    for item in items[:MAX_DIR_ENTRIES]:
        prefix = "[DIR] " if item.is_dir() else "[FILE]"
        try:
            size = f" ({item.stat().st_size} bytes)" if not item.is_dir() else ""
        except OSError:
            size = ""
        output.append(f"{prefix} {item.name}{size}")

    if truncated:
        output.append(f"... [{len(items) - MAX_DIR_ENTRIES} more entries omitted]")
    return "\n".join(output)


async def read_file(
    path: str,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    workspace_dir: Optional[Path] = None,
    container_id: Optional[str] = None,
) -> str:
    """Reads the content of a file, optionally slicing specific lines (1-indexed).

    Args:
        path: Path to the file, relative to the workspace root.
        start_line: Optional starting line number (1-indexed, inclusive).
        end_line: Optional ending line number (1-indexed, inclusive).
    """
    if container_id:
        script = _SANDBOX_PRELUDE + f"""
path_str = {path!r}
target = _confine(path_str)

if not target.exists():
    print(f"Error: File '{{path_str}}' does not exist.")
    sys.exit(0)
if target.is_dir():
    print(f"Error: '{{path_str}}' is a directory, not a file.")
    sys.exit(0)

try:
    content = target.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines(keepends=True)
    start_line = {start_line!r}
    end_line = {end_line!r}
    max_lines = {MAX_FILE_LINES}
    max_chars = {MAX_FILE_CHARS}
    if start_line is not None or end_line is not None:
        s = max(1, start_line) if start_line else 1
        e = min(len(lines), end_line) if end_line else len(lines)
        body = "".join([f"{{i + s:4d}} | {{line}}" for i, line in enumerate(lines[s - 1:e])])
    elif len(lines) <= max_lines:
        body = "".join([f"{{i + 1:4d}} | {{line}}" for i, line in enumerate(lines)])
    else:
        body = f"File has {{len(lines)}} lines. Showing first {{max_lines}} lines:\\n" + "".join(
            [f"{{i + 1:4d}} | {{line}}" for i, line in enumerate(lines[:max_lines])]
        )
    if len(body) > max_chars:
        body = body[:max_chars] + f"\\n... [truncated at {{max_chars}} characters; read a specific line range to see more]"
    print(body, end="")
except Exception as e:
    print(f"Error reading file '{{path_str}}': {{str(e)}}")
"""
        return await sandbox_manager.exec_python(container_id, script)

    try:
        file_path = resolve_workspace_path(path, workspace_dir)
    except PathOutsideWorkspaceError as e:
        return str(e)

    if not file_path.exists():
        return f"Error: File '{path}' does not exist."
    if file_path.is_dir():
        return f"Error: '{path}' is a directory, not a file."

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines(keepends=True)
        return _format_file_lines(lines, start_line, end_line)
    except Exception as e:
        return f"Error reading file '{path}': {str(e)}"


async def write_file(
    path: str,
    content: str,
    workspace_dir: Optional[Path] = None,
    container_id: Optional[str] = None,
) -> str:
    """Creates a new file or overwrites an existing file with the provided content.

    Args:
        path: Path where the file should be saved, relative to the workspace root.
        content: Exact text content to write into the file.
    """
    if container_id:
        script = _SANDBOX_PRELUDE + f"""
path_str = {path!r}
content = {content!r}
target = _confine(path_str)

try:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    print(f"Successfully wrote {{len(content)}} characters to '{{path_str}}'.")
except Exception as e:
    print(f"Error writing to file '{{path_str}}': {{str(e)}}")
"""
        return await sandbox_manager.exec_python(container_id, script)

    try:
        file_path = resolve_workspace_path(path, workspace_dir)
    except PathOutsideWorkspaceError as e:
        return str(e)

    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return f"Successfully wrote {len(content)} characters to '{path}'."
    except Exception as e:
        return f"Error writing to file '{path}': {str(e)}"


async def edit_file(
    path: str,
    target: str,
    replacement: str,
    workspace_dir: Optional[Path] = None,
    container_id: Optional[str] = None,
) -> str:
    """Edits an existing file by replacing an exact occurrence of target text with replacement text.

    Args:
        path: Path to the file to edit, relative to the workspace root.
        target: The exact text sequence currently in the file to be replaced.
        replacement: The new replacement text.
    """
    if container_id:
        script = _SANDBOX_PRELUDE + f"""
path_str = {path!r}
target_str = {target!r}
replacement_str = {replacement!r}
target_path = _confine(path_str)

if not target_path.exists():
    print(f"Error: File '{{path_str}}' does not exist.")
    sys.exit(0)

try:
    content = target_path.read_text(encoding="utf-8")
    count = content.count(target_str)
    if count == 0:
        print(f"Error: Target text not found in '{{path_str}}'. Make sure whitespace and indentation match exactly.")
        sys.exit(0)
    if count > 1:
        print(f"Error: Target text occurs {{count}} times in '{{path_str}}'. Please provide more surrounding context to ensure a unique match.")
        sys.exit(0)

    target_path.write_text(content.replace(target_str, replacement_str, 1), encoding="utf-8")
    print(f"Successfully updated '{{path_str}}'.")
except Exception as e:
    print(f"Error editing file '{{path_str}}': {{str(e)}}")
"""
        return await sandbox_manager.exec_python(container_id, script)

    try:
        file_path = resolve_workspace_path(path, workspace_dir)
    except PathOutsideWorkspaceError as e:
        return str(e)

    if not file_path.exists():
        return f"Error: File '{path}' does not exist."

    try:
        content = file_path.read_text(encoding="utf-8")
        count = content.count(target)
        if count == 0:
            return f"Error: Target text not found in '{path}'. Make sure whitespace and indentation match exactly."
        if count > 1:
            return f"Error: Target text occurs {count} times in '{path}'. Please provide more surrounding context to ensure a unique match."

        file_path.write_text(content.replace(target, replacement, 1), encoding="utf-8")
        return f"Successfully updated '{path}'."
    except Exception as e:
        return f"Error editing file '{path}': {str(e)}"


async def list_directory(
    path: Optional[str] = ".",
    workspace_dir: Optional[Path] = None,
    container_id: Optional[str] = None,
) -> str:
    """Lists files and directories in the specified path.

    Args:
        path: Directory path to list, relative to the workspace root.
    """
    if container_id:
        script = _SANDBOX_PRELUDE + f"""
path_str = {(path or ".")!r}
target = _confine(path_str)

if not target.exists():
    print(f"Error: Directory '{{path_str}}' does not exist.")
    sys.exit(0)
if not target.is_dir():
    print(f"Error: '{{path_str}}' is a file, not a directory.")
    sys.exit(0)

try:
    items = sorted(target.iterdir())
    max_entries = {MAX_DIR_ENTRIES}
    output = []
    for item in items[:max_entries]:
        prefix = "[DIR] " if item.is_dir() else "[FILE]"
        try:
            size = f" ({{item.stat().st_size}} bytes)" if not item.is_dir() else ""
        except OSError:
            size = ""
        output.append(f"{{prefix}} {{item.name}}{{size}}")
    if len(items) > max_entries:
        output.append(f"... [{{len(items) - max_entries}} more entries omitted]")
    print("\\n".join(output) if output else f"Directory '{{path_str}}' is empty.")
except Exception as e:
    print(f"Error listing directory '{{path_str}}': {{str(e)}}")
"""
        return await sandbox_manager.exec_python(container_id, script)

    try:
        dir_path = resolve_workspace_path(path or ".", workspace_dir)
    except PathOutsideWorkspaceError as e:
        return str(e)

    if not dir_path.exists():
        return f"Error: Directory '{path}' does not exist."
    if not dir_path.is_dir():
        return f"Error: '{path}' is a file, not a directory."

    try:
        items = sorted(dir_path.iterdir())
        return _format_dir_entries(items, path or ".")
    except Exception as e:
        return f"Error listing directory '{path}': {str(e)}"
