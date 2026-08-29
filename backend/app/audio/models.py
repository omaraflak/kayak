from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class AudioKind(str, Enum):
    """What a stored item is."""
    #: Audio generated from text.
    CLIP = "clip"
    #: Text produced from a recording. Holds no audio of its own.
    TRANSCRIPT = "transcript"


class AudioItem(BaseModel):
    """One thing the Audio page has produced."""
    id: str
    kind: AudioKind
    #: Repository that produced it, so a clip made with one voice model is still
    #: identifiable after the server has moved on to another.
    model_id: str
    #: The spoken text for a clip, or the transcript for a transcription.
    text: str
    voice: Optional[str] = None
    language: Optional[str] = None
    #: Name of the stored audio file. Absent for transcripts.
    filename: Optional[str] = None
    size_bytes: int = 0
    duration_seconds: Optional[float] = None
    #: Name of the file the user uploaded, for transcripts.
    source_filename: Optional[str] = None
    created_at: float


class JobState(str, Enum):
    """Where a synthesis job has got to."""
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SynthesisJob(BaseModel):
    """One queued or running synthesis.

    Held by the server rather than the page, so leaving the Audio tab -- or
    closing it -- does not abandon work the container is already doing.
    """
    id: str
    text: str
    voice: Optional[str] = None
    speed: float = 1.0
    response_format: str = "wav"
    model_id: str
    state: JobState
    #: Chunks the server has spoken, and how many it split the text into. Both
    #: zero until the first progress reading arrives.
    chunks_done: int = 0
    chunks_total: int = 0
    #: The stored clip, once there is one.
    item_id: Optional[str] = None
    error: Optional[str] = None
    created_at: float
    started_at: Optional[float] = None
    finished_at: Optional[float] = None


class SpeechRequest(BaseModel):
    """What the Audio page sends to have something spoken."""
    text: str = Field(..., min_length=1)
    voice: Optional[str] = None
    speed: float = Field(1.0, gt=0.25, le=4.0)
    response_format: str = "wav"


class Voice(BaseModel):
    """A voice the running speech model offers."""
    id: str
    label: str
    language: Optional[str] = None


class VoiceList(BaseModel):
    """Voices, and which model they belong to.

    The model id travels with them so a stale list -- fetched before the server was
    switched to a different model -- is recognisable as stale rather than silently
    offering voices the loaded model has never heard of.
    """
    model_id: Optional[str] = None
    voices: List[Voice] = []
    #: Voice the server uses when none is named, so the page opens on it.
    default_voice: Optional[str] = None
