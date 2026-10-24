"""Filesystem and terminal access to a conversation's container.

The workspace directory is bind-mounted into the conversation's container at
`/workspace`, so browsing, previewing and uploading go through the host directory
directly -- no exec round-trip -- while everything appears instantly inside the
container. The terminal, by contrast, genuinely executes inside the container:
that is the whole point of offering it.
"""

import asyncio
from datetime import datetime, timezone
import errno
import json
import logging
import mimetypes
from pathlib import Path, PurePosixPath
import shutil
import threading
from typing import List, Optional
from fastapi import APIRouter, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel
from backend.app.agent.sandbox import sandbox_manager
from backend.app.config import settings
from backend.app.database import get_conversation
from backend.app.routes.auth import (
    SESSION_COOKIE_NAME,
    TOKEN_HEADER_NAME,
    matches_session,
    matches_token,
)
from backend.app.routes.conversations import load_conversation
from backend.app.agent.turns import ensure_sandbox
from backend.app.user_errors import describe_sandbox_error
from backend.app.tools.builtins.file_tools import (
    PathOutsideWorkspaceError,
    resolve_workspace_path,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/conversations/{conversation_id}", tags=["workspace"])

#: Types the browser may render inline. Everything else downloads: serving
#: agent-written HTML inline would execute its scripts on Kayak's own origin.
_INLINE_PREFIXES = ("image/", "text/", "audio/", "video/")
_INLINE_TYPES = frozenset({"application/pdf", "application/json"})


class FileEntry(BaseModel):
    """One directory entry in the conversation's workspace."""
    name: str
    is_dir: bool
    size: int
    modified_at: str


class DirectoryListing(BaseModel):
    """Contents of one workspace directory."""
    path: str
    entries: List[FileEntry]


def safe_upload_path(raw: str) -> PurePosixPath:
    """Normalises an uploaded file's relative path, refusing escapes.

    Folder uploads send paths like `project/src/main.py`; nothing about them can
    be trusted. Absolute paths and parent references are rejected outright rather
    than silently rewritten, so a hostile name cannot land outside the workspace.

    Raises:
        ValueError: If the path is absolute, empty, or contains '..'.
    """
    path = PurePosixPath(raw)
    parts = [part for part in path.parts if part not in (".", "")]
    if path.is_absolute() or not parts or any(part == ".." for part in parts):
        raise ValueError(
            "One of these files has a name Kayak can't use. Rename it and try again."
        )
    return PurePosixPath(*parts)


def preview_disposition(filename: str) -> tuple[str, bool]:
    """Chooses a media type and whether the browser may render it inline.

    HTML is the deliberate exception to inline text: rendering agent-written HTML
    on Kayak's origin would run its scripts with access to the API.
    """
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    if media_type in ("text/html", "application/xhtml+xml"):
        return "text/plain", True
    inline = media_type.startswith(_INLINE_PREFIXES) or media_type in _INLINE_TYPES
    return media_type, inline


def _resolve_or_400(conversation_id: str, path: str) -> Path:
    """Resolves a workspace-relative path, translating escapes into a 400."""
    workspace = settings.WORKSPACES_DIR / conversation_id
    try:
        return resolve_workspace_path(path, workspace)
    except PathOutsideWorkspaceError:
        raise HTTPException(
            status_code=400,
            detail="This points outside the conversation's files, so Kayak won't open it.",
        )


@router.get("/fs", response_model=DirectoryListing)
async def list_workspace_directory(
    conversation_id: str, path: str = "."
) -> DirectoryListing:
    """Lists one directory of the conversation's workspace."""
    await load_conversation(conversation_id)
    target = _resolve_or_400(conversation_id, path)

    if not target.exists():
        raise HTTPException(status_code=404, detail="This folder no longer exists.")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="This is a file, not a folder.")

    entries: List[FileEntry] = []
    for item in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        try:
            stat = item.stat()
        except OSError:
            continue
        entries.append(
            FileEntry(
                name=item.name,
                is_dir=item.is_dir(),
                size=0 if item.is_dir() else stat.st_size,
                modified_at=datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat(),
            )
        )

    return DirectoryListing(path=path, entries=entries)


@router.get("/fs/file")
async def read_workspace_file(conversation_id: str, path: str) -> FileResponse:
    """Serves one workspace file for preview or download."""
    await load_conversation(conversation_id)
    target = _resolve_or_400(conversation_id, path)

    if not target.exists() or target.is_dir():
        raise HTTPException(status_code=404, detail="This file no longer exists.")

    media_type, inline = preview_disposition(target.name)
    return FileResponse(
        str(target),
        media_type=media_type,
        # Starlette encodes the name itself (RFC 5987). Writing the header by hand
        # failed on any name outside Latin-1 -- an emoji, Japanese, or the narrow
        # no-break space in every macOS screenshot name -- with a server error.
        filename=target.name,
        content_disposition_type="inline" if inline else "attachment",
        # Never let the browser second-guess the type into something executable.
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.post("/fs/upload")
async def upload_workspace_files(
    conversation_id: str, files: List[UploadFile]
) -> dict:
    """Saves uploaded files (or a folder's worth of them) into the workspace.

    Each part's filename carries its path relative to the workspace root; folder
    uploads use this to preserve their structure. The workspace is mounted in the
    container, so the files are visible to the agent immediately.
    """
    await load_conversation(conversation_id)

    saved = 0
    for upload in files:
        try:
            relative = safe_upload_path(upload.filename or "")
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))

        target = _resolve_or_400(conversation_id, str(relative))
        try:
            await asyncio.get_running_loop().run_in_executor(
                None, _save_upload, upload, target
            )
        except OSError as error:
            if error.errno == errno.ENOSPC:
                raise HTTPException(
                    status_code=507,
                    detail="Your Mac is out of disk space. Free some space, then try again.",
                )
            logger.warning("Could not save upload %s: %s", target, error)
            raise HTTPException(
                status_code=500, detail="Kayak couldn't save these files. Try again."
            )
        saved += 1

    return {"status": "uploaded", "count": saved}


def _save_upload(upload: UploadFile, target: Path) -> None:
    """Copies an upload to its place in the workspace, a piece at a time.

    Reading the whole upload into memory first meant a large file could take the
    Kayak server down with it, and the sandboxes and models it was looking after.
    A copy that fails part-way is removed, not left looking complete.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("wb") as destination:
            shutil.copyfileobj(upload.file, destination, 1024 * 1024)
    except OSError:
        target.unlink(missing_ok=True)
        raise


def _websocket_authorized(websocket: WebSocket) -> bool:
    """Applies the shared-secret check to a WebSocket.

    The HTTP auth middleware never sees WebSocket upgrades, so without this the
    terminal would be an unauthenticated shell on any deployment that set a token.
    """
    if not settings.AUTH_TOKEN:
        return True
    return matches_token(websocket.headers.get(TOKEN_HEADER_NAME)) or matches_session(
        websocket.cookies.get(SESSION_COOKIE_NAME)
    )


async def _send_terminal_error(websocket: WebSocket, message: str) -> None:
    try:
        await websocket.send_text(json.dumps({"type": "error", "data": message}))
    except Exception:
        pass


@router.websocket("/terminal")
async def terminal_socket(websocket: WebSocket, conversation_id: str) -> None:
    """Bridges a browser terminal to an interactive shell inside the container.

    The client sends keystrokes as text/binary frames and `{"type": "resize"}`
    control messages; everything the PTY emits streams back as binary frames.
    """
    await websocket.accept()

    if not _websocket_authorized(websocket):
        await websocket.close(code=4401)
        return

    conversation = await get_conversation(conversation_id)
    if not conversation:
        await websocket.close(code=4404)
        return

    try:
        container_id = await ensure_sandbox(conversation)
        shell = await sandbox_manager.open_shell(container_id)
    except Exception as error:
        await _send_terminal_error(websocket, describe_sandbox_error(error))
        await websocket.close()
        return

    loop = asyncio.get_running_loop()
    output: "asyncio.Queue[Optional[bytes]]" = asyncio.Queue()

    def read_shell() -> None:
        # A thread of its own rather than one from the shared pool: this blocks for
        # as long as the terminal is open, and a few open terminals used to take
        # every pooled thread, leaving tool calls -- and shutdown, which stops the
        # sandboxes through the same pool -- waiting on nothing.
        try:
            while True:
                data = shell.read()
                loop.call_soon_threadsafe(output.put_nowait, data or None)
                if not data:
                    return
        except Exception:
            loop.call_soon_threadsafe(output.put_nowait, None)

    threading.Thread(target=read_shell, name="terminal-reader", daemon=True).start()

    async def pump_shell_output() -> None:
        try:
            while True:
                data = await output.get()
                if not data:
                    break
                await websocket.send_bytes(data)
        except Exception:
            pass
        finally:
            try:
                await websocket.close()
            except Exception:
                pass

    output_task = asyncio.create_task(pump_shell_output())

    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break

            payload = message.get("bytes")
            text = message.get("text")
            if text is not None:
                if text.startswith("{"):
                    # Control messages ride the same socket as keystrokes, the
                    # way the client distinguishes them is a leading brace.
                    try:
                        control = json.loads(text)
                        if control.get("type") == "resize":
                            await loop.run_in_executor(
                                None,
                                shell.resize,
                                int(control["rows"]),
                                int(control["cols"]),
                            )
                            continue
                    except (ValueError, KeyError, TypeError):
                        pass
                payload = text.encode()

            if payload:
                await loop.run_in_executor(None, shell.write, payload)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.warning("Terminal session for %s ended abnormally", conversation_id, exc_info=True)
    finally:
        # Closing the socket first unblocks the reader thread parked in recv();
        # cancelling the task alone would leave that thread stuck forever.
        await loop.run_in_executor(None, shell.close)
        output_task.cancel()
