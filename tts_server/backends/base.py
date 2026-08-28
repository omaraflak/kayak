"""What every speech backend must provide."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional, Tuple

if TYPE_CHECKING:  # numpy lives in the image, not wherever dispatch is inspected.
    import numpy as np


@dataclass(frozen=True)
class Voice:
    """One voice a loaded model can speak in.

    Models differ enormously here: some publish a named set, some take a speaker
    embedding, some clone from a reference clip, and some have exactly one voice.
    The server reports what the loaded model actually offers rather than assuming,
    so no client carries a list that only fits one model.
    """
    id: str
    label: str
    #: Language tag when the model organises voices by language, e.g. "en-us".
    language: Optional[str] = None


class SpeechBackend(ABC):
    """Loads one repository and turns text into audio."""

    #: Human-readable backend name, used in errors and in /v1/models.
    name: str

    def __init__(self, model_id: str, trust_remote_code: bool = False):
        self.model_id = model_id
        self.trust_remote_code = trust_remote_code

    @classmethod
    @abstractmethod
    def claims(cls, model_id: str) -> bool:
        """Whether this backend can load the repository, by id alone.

        Called before anything is downloaded, so it may only look at the id. The
        server's own metadata lookup happens on Kayak's side, where the Hub is
        already being queried for the catalogue.
        """

    @abstractmethod
    def load(self) -> None:
        """Downloads and loads the model. Called once, at startup."""

    @abstractmethod
    def voices(self) -> List[Voice]:
        """Voices the loaded model offers. Empty when it has no notion of them."""

    @abstractmethod
    def synthesize(
        self, text: str, voice: Optional[str], speed: float
    ) -> Tuple[np.ndarray, int]:
        """Speaks one piece of text.

        Args:
            text: The text to speak. Already chunked to a length the model accepts.
            voice: One of the ids from :meth:`voices`, or None for the default.
            speed: Playback rate multiplier, 1.0 being the model's natural pace.

        Returns:
            Tuple of mono float32 samples in [-1, 1] and their sample rate.
        """
