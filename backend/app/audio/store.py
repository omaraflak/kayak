"""Where generated clips and transcripts live.

Files on disk with a JSON sidecar each, rather than rows in a table or one index
file. Two reasons: the audio has to be a real file anyway for the browser to stream
it, and a per-item sidecar cannot be corrupted by a concurrent write the way a single
index can -- a half-written index would lose every clip, not one.

The same store backs the Audio page and, later, anything else that produces audio, so
a clip an agent generates and a clip a person generates land in the same place.
"""

import json
import logging
import time
import uuid
from pathlib import Path
from typing import List, Optional

from backend.app.audio.models import AudioItem, AudioKind
from backend.app.config import settings

logger = logging.getLogger(__name__)

#: Extensions the store will serve. A file that arrives with anything else is not
#: written, so the download route can never be talked into serving a script.
_ALLOWED_SUFFIXES = {".wav", ".flac", ".ogg", ".mp3", ".m4a", ".webm"}

_MEDIA_TYPES = {
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".webm": "audio/webm",
}


class AudioStoreError(RuntimeError):
    """Raised when a request names something the store will not do."""


def root() -> Path:
    """Directory holding every stored item."""
    path = settings.DATA_DIR / "audio"
    path.mkdir(parents=True, exist_ok=True)
    return path


def media_type_for(suffix: str) -> str:
    return _MEDIA_TYPES.get(suffix.lower(), "application/octet-stream")


def _sidecar(item_id: str) -> Path:
    return root() / f"{item_id}.json"


def _resolve_id(item_id: str) -> str:
    """Validates an id before it is used to build a path.

    Ids come from URLs, so treating one as a filename without checking is a path
    traversal. They are generated as hex uuids; anything else is refused outright
    rather than sanitised, since there is no legitimate id this rejects.
    """
    if not item_id or not all(character in "0123456789abcdef" for character in item_id):
        raise AudioStoreError(f"'{item_id}' is not a valid item id.")
    return item_id


def save_audio(
    *,
    data: bytes,
    suffix: str,
    kind: AudioKind,
    model_id: str,
    text: str,
    voice: Optional[str] = None,
    duration_seconds: Optional[float] = None,
    source_filename: Optional[str] = None,
) -> AudioItem:
    """Writes an audio file and its metadata, returning the stored item."""
    suffix = suffix if suffix.startswith(".") else f".{suffix}"
    if suffix.lower() not in _ALLOWED_SUFFIXES:
        raise AudioStoreError(f"'{suffix}' is not an audio format this store keeps.")

    item_id = uuid.uuid4().hex
    audio_path = root() / f"{item_id}{suffix.lower()}"
    audio_path.write_bytes(data)

    item = AudioItem(
        id=item_id,
        kind=kind,
        model_id=model_id,
        text=text,
        voice=voice,
        filename=audio_path.name,
        size_bytes=len(data),
        duration_seconds=duration_seconds,
        source_filename=source_filename,
        created_at=time.time(),
    )
    _sidecar(item_id).write_text(item.model_dump_json(indent=2), encoding="utf-8")
    return item


def save_transcript(
    *,
    text: str,
    model_id: str,
    source_filename: str,
    language: Optional[str] = None,
) -> AudioItem:
    """Records a transcription result. The uploaded recording is not kept.

    Keeping it would double the disk cost of every transcription for something the
    user already has a copy of, and it is their recording rather than ours.
    """
    item_id = uuid.uuid4().hex
    item = AudioItem(
        id=item_id,
        kind=AudioKind.TRANSCRIPT,
        model_id=model_id,
        text=text,
        language=language,
        source_filename=source_filename,
        created_at=time.time(),
    )
    _sidecar(item_id).write_text(item.model_dump_json(indent=2), encoding="utf-8")
    return item


def list_items(kind: Optional[AudioKind] = None) -> List[AudioItem]:
    """Every stored item, newest first."""
    items: List[AudioItem] = []
    for sidecar in root().glob("*.json"):
        try:
            items.append(AudioItem.model_validate_json(sidecar.read_text(encoding="utf-8")))
        except Exception:
            # One unreadable sidecar -- a half-written file, or one from an older
            # shape -- must not empty the whole list.
            logger.warning("Ignoring unreadable audio metadata at %s", sidecar)

    if kind is not None:
        items = [item for item in items if item.kind == kind]
    return sorted(items, key=lambda item: item.created_at, reverse=True)


def get_item(item_id: str) -> AudioItem:
    """One stored item.

    Raises:
        AudioStoreError: If the id is malformed.
        FileNotFoundError: If nothing is stored under it.
    """
    sidecar = _sidecar(_resolve_id(item_id))
    if not sidecar.is_file():
        raise FileNotFoundError(f"No stored audio with id '{item_id}'.")
    return AudioItem.model_validate_json(sidecar.read_text(encoding="utf-8"))


def audio_path(item: AudioItem) -> Path:
    """Path of an item's audio file.

    Raises:
        FileNotFoundError: If the item has no audio, or the file has gone missing.
    """
    if not item.filename:
        raise FileNotFoundError(f"Item '{item.id}' has no audio file.")
    path = root() / item.filename
    if not path.is_file():
        raise FileNotFoundError(f"The audio for '{item.id}' is no longer on disk.")
    return path


def delete_item(item_id: str) -> None:
    """Removes an item and its audio.

    Raises:
        AudioStoreError: If the id is malformed.
        FileNotFoundError: If nothing is stored under it.
    """
    item = get_item(item_id)
    if item.filename:
        (root() / item.filename).unlink(missing_ok=True)
    _sidecar(item.id).unlink(missing_ok=True)
