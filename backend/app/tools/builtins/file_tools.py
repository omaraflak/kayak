import asyncio
import json
import os
from pathlib import Path
from typing import Optional
from backend.app.agent.sandbox import sandbox_manager


def _resolve_path(path_str: str, workspace_dir: Optional[Path]) -> Path:
    base = workspace_dir if workspace_dir else Path.cwd()
    target = Path(path_str)
    if not target.is_absolute():
        target = (base / target).resolve()
    return target


async def read_file(
    path: str,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    workspace_dir: Optional[Path] = None,
    container_id: Optional[str] = None,
) -> str:
    """Reads the content of a file, optionally slicing specific lines (1-indexed).

    Args:
        path: Relative or absolute path to the file.
        start_line: Optional starting line number (1-indexed, inclusive).
        end_line: Optional ending line number (1-indexed, inclusive).
    """
    if container_id:
        script = f"""
import sys
from pathlib import Path

path_str = {repr(path)}
target = Path(path_str)
if not target.is_absolute():
    target = (Path('/workspace') / target).resolve()

if not target.exists():
    print(f"Error: File '{{path_str}}' does not exist.")
    sys.exit(0)
if target.is_dir():
    print(f"Error: '{{path_str}}' is a directory, not a file.")
    sys.exit(0)

try:
    content = target.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines(keepends=True)
    start_line = {repr(start_line)}
    end_line = {repr(end_line)}
    if start_line is not None or end_line is not None:
        s = max(1, start_line) if start_line else 1
        e = min(len(lines), end_line) if end_line else len(lines)
        sliced_lines = lines[s - 1 : e]
        print("".join([f"{{i + s:4d}} | {{line}}" for i, line in enumerate(sliced_lines)]), end="")
    elif len(lines) <= 500:
        print("".join([f"{{i + 1:4d}} | {{line}}" for i, line in enumerate(lines)]), end="")
    else:
        print(f"File has {{len(lines)}} lines. Showing first 500 lines:\\n" + "".join([f"{{i + 1:4d}} | {{line}}" for i, line in enumerate(lines[:500])]), end="")
except Exception as e:
    print(f"Error reading file '{{path_str}}': {{str(e)}}")
"""
        return await sandbox_manager.exec_python(container_id, script)

    file_path = _resolve_path(path, workspace_dir)
    if not file_path.exists():
        return f"Error: File '{path}' does not exist."
    if file_path.is_dir():
        return f"Error: '{path}' is a directory, not a file."

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines(keepends=True)

        if start_line is not None or end_line is not None:
            s = max(1, start_line) if start_line else 1
            e = min(len(lines), end_line) if end_line else len(lines)
            sliced_lines = lines[s - 1 : e]
            numbered = [
                f"{i + s:4d} | {line}" for i, line in enumerate(sliced_lines)
            ]
            return "".join(numbered)

        if len(lines) <= 500:
            numbered = [
                f"{i + 1:4d} | {line}" for i, line in enumerate(lines)
            ]
            return "".join(numbered)
        return (
            f"File has {len(lines)} lines. Showing first 500 lines:\n"
            + "".join(
                f"{i + 1:4d} | {line}" for i, line in enumerate(lines[:500])
            )
        )
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
        path: Path where the file should be saved.
        content: Exact text content to write into the file.
    """
    if container_id:
        script = f"""
import sys
from pathlib import Path

path_str = {repr(path)}
content = {repr(content)}
target = Path(path_str)
if not target.is_absolute():
    target = (Path('/workspace') / target).resolve()

try:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    print(f"Successfully wrote {{len(content)}} characters to '{{path_str}}'.")
except Exception as e:
    print(f"Error writing to file '{{path_str}}': {{str(e)}}")
"""
        return await sandbox_manager.exec_python(container_id, script)

    file_path = _resolve_path(path, workspace_dir)
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
        path: Path to the file to edit.
        target: The exact text sequence currently in the file to be replaced.
        replacement: The new replacement text.
    """
    if container_id:
        script = f"""
import sys
from pathlib import Path

path_str = {repr(path)}
target_str = {repr(target)}
replacement_str = {repr(replacement)}

target = Path(path_str)
if not target.is_absolute():
    target = (Path('/workspace') / target).resolve()

if not target.exists():
    print(f"Error: File '{{path_str}}' does not exist.")
    sys.exit(0)

try:
    content = target.read_text(encoding="utf-8")
    if target_str not in content:
        print(f"Error: Target text not found in '{{path_str}}'. Make sure whitespace and indentation match exactly.")
        sys.exit(0)

    count = content.count(target_str)
    if count > 1:
        print(f"Error: Target text occurs {{count}} times in '{{path_str}}'. Please provide more surrounding context to ensure a unique match.")
        sys.exit(0)

    new_content = content.replace(target_str, replacement_str, 1)
    target.write_text(new_content, encoding="utf-8")
    print(f"Successfully updated '{{path_str}}'.")
except Exception as e:
    print(f"Error editing file '{{path_str}}': {{str(e)}}")
"""
        return await sandbox_manager.exec_python(container_id, script)

    file_path = _resolve_path(path, workspace_dir)
    if not file_path.exists():
        return f"Error: File '{path}' does not exist."

    try:
        content = file_path.read_text(encoding="utf-8")
        if target not in content:
            return f"Error: Target text not found in '{path}'. Make sure whitespace and indentation match exactly."

        count = content.count(target)
        if count > 1:
            return f"Error: Target text occurs {count} times in '{path}'. Please provide more surrounding context to ensure a unique match."

        new_content = content.replace(target, replacement, 1)
        file_path.write_text(new_content, encoding="utf-8")
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
        path: Directory path to list (defaults to workspace root).
    """
    if container_id:
        script = f"""
import sys
from pathlib import Path

path_str = {repr(path or ".")}
target = Path(path_str)
if not target.is_absolute():
    target = (Path('/workspace') / target).resolve()

if not target.exists():
    print(f"Error: Directory '{{path_str}}' does not exist.")
    sys.exit(0)
if not target.is_dir():
    print(f"Error: '{{path_str}}' is a file, not a directory.")
    sys.exit(0)

try:
    items = sorted(list(target.iterdir()))
    output = []
    for item in items:
        prefix = "[DIR] " if item.is_dir() else "[FILE]"
        size = f" ({{item.stat().st_size}} bytes)" if not item.is_dir() else ""
        output.append(f"{{prefix}} {{item.name}}{{size}}")
    if not output:
        print(f"Directory '{{path_str}}' is empty.")
    else:
        print("\\n".join(output))
except Exception as e:
    print(f"Error listing directory '{{path_str}}': {{str(e)}}")
"""
        return await sandbox_manager.exec_python(container_id, script)

    dir_path = _resolve_path(path or ".", workspace_dir)
    if not dir_path.exists():
        return f"Error: Directory '{path}' does not exist."
    if not dir_path.is_dir():
        return f"Error: '{path}' is a file, not a directory."

    try:
        items = sorted(list(dir_path.iterdir()))
        output = []
        for item in items:
            prefix = "[DIR] " if item.is_dir() else "[FILE]"
            size = (
                f" ({item.stat().st_size} bytes)" if not item.is_dir() else ""
            )
            output.append(f"{prefix} {item.name}{size}")

        if not output:
            return f"Directory '{path}' is empty."
        return "\n".join(output)
    except Exception as e:
        return f"Error listing directory '{path}': {str(e)}"
