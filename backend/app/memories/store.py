"""What Kayak has learned from the user.

Kept separate from skills on purpose. A skill is an authored capability -- curated,
versioned, loaded when it is relevant. A memory is a fact or a correction picked up in
passing, which has to be present on *every* turn and has to be cheap to append. Storing
them together would turn the skill editor into a junk drawer, and would make "did the
agent actually see this?" depend on load semantics.

Memories are shared by every agent rather than scoped to a profile: they describe the
user, not the agent that happened to be listening. Something you correct once should
not have to be repeated to each profile in turn.

The file is the source of truth and is meant to be readable and editable by hand:
plain markdown bullets, one memory per line, oldest first. Everything Kayak persists
lives in files the user can open, and this is no exception -- what the app believes
about you should never be locked inside a database.
"""

import logging
from typing import List

from backend.app.config import settings

logger = logging.getLogger(__name__)

#: Ceiling on what is carried into every prompt. Memories are injected on every turn,
#: so an unbounded file would quietly eat the context window -- and the symptom would
#: be a model getting worse, with nothing pointing at the cause.
MAX_MEMORY_CHARS = 4000

_HEADING = "# Memories"

_PREAMBLE = (
    "Things Kayak has learned from you. Every agent reads these. One per line;"
    " edit or delete freely."
)


def _parse(content: str) -> List[str]:
    """Reads memories out of the markdown file, ignoring anything that is not a bullet.

    Being lenient here is deliberate: the file invites hand editing, and a stray note
    or heading someone adds must not destroy the list or crash a turn.
    """
    memories: List[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("- ") and len(stripped) > 2:
            memories.append(stripped[2:].strip())
    return memories


def _render(memories: List[str]) -> str:
    """Formats memories as the markdown file that stores them."""
    lines = [_HEADING, "", _PREAMBLE, ""]
    lines.extend(f"- {memory}" for memory in memories)
    return "\n".join(lines) + "\n"


def _normalise(content: str) -> str:
    """Collapses a memory to a single tidy line.

    Memories are stored one per line, so an embedded newline would silently split one
    memory into a fragment plus some text that no longer parses as a bullet at all.
    """
    return " ".join(content.split()).strip()


def trim_to_budget(memories: List[str], max_chars: int = MAX_MEMORY_CHARS) -> List[str]:
    """Drops the oldest memories until the rest fit in the prompt budget.

    Oldest-first because a correction the user gave today describes them better than
    one from a month ago, and because dropping the newest would make teaching Kayak
    something appear to silently fail.
    """
    kept: List[str] = []
    used = 0
    for memory in reversed(memories):
        cost = len(memory) + 2  # the "- " that carries it
        if used + cost > max_chars:
            break
        kept.append(memory)
        used += cost
    kept.reverse()
    return kept


class MemoryStore:
    """Reads and writes the memories every agent shares."""

    def list_memories(self) -> List[str]:
        """Everything Kayak has been taught, oldest first."""
        path = settings.MEMORY_FILE
        if not path.exists():
            return []
        try:
            return _parse(path.read_text(encoding="utf-8"))
        except OSError:
            logger.exception("Could not read memories from %s", path)
            return []

    def replace(self, memories: List[str]) -> List[str]:
        """Overwrites the memories, returning what was stored.

        Blank entries are dropped and duplicates collapsed, so editing in the UI
        cannot leave empty bullets or the same lesson twice.
        """
        cleaned: List[str] = []
        for memory in memories:
            text = _normalise(memory)
            if text and text not in cleaned:
                cleaned.append(text)

        path = settings.MEMORY_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render(cleaned), encoding="utf-8")
        return cleaned

    def add(self, content: str) -> List[str]:
        """Teaches Kayak one thing, returning every memory afterwards.

        Raises:
            ValueError: If the memory is empty.
        """
        text = _normalise(content)
        if not text:
            raise ValueError("A memory cannot be empty.")

        memories = [entry for entry in self.list_memories() if entry != text]
        memories.append(text)
        return self.replace(trim_to_budget(memories))

    def prompt_section(self) -> str:
        """The memories as they appear in every agent's system prompt.

        Returns an empty string when there is nothing to say, so the prompt does not
        carry a heading with nothing under it.
        """
        memories = trim_to_budget(self.list_memories())
        if not memories:
            return ""

        lines = [
            "\n## What you have learned from this user",
            "These are things the user taught you in earlier conversations. Treat them"
            " as standing instructions, and prefer them over your own defaults.",
        ]
        lines.extend(f"- {memory}" for memory in memories)
        return "\n".join(lines)


memory_store = MemoryStore()
