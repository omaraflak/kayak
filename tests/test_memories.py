"""Tests for what Kayak remembers between conversations.

Memories are shared by every agent, injected into every prompt, written by an agent
mid-turn, and meant to be edited by hand in the file. Each of those is a way the list
can be corrupted, so each is covered here.
"""

from pathlib import Path

import pytest

from backend.app.config import settings
from backend.app.memories.store import MAX_MEMORY_CHARS, MemoryStore, trim_to_budget


@pytest.fixture
def store(tmp_path: Path, monkeypatch) -> MemoryStore:
    monkeypatch.setattr(settings, "MEMORY_FILE", tmp_path / "memories.md")
    return MemoryStore()


class TestTeaching:
    def test_a_fresh_install_knows_nothing(self, store: MemoryStore):
        assert store.list_memories() == []

    def test_what_is_taught_is_remembered(self, store: MemoryStore):
        store.add("Prefers tabs over spaces.")

        assert store.list_memories() == ["Prefers tabs over spaces."]

    def test_memories_are_kept_in_the_order_they_were_learned(self, store: MemoryStore):
        store.add("First lesson.")
        store.add("Second lesson.")

        assert store.list_memories() == ["First lesson.", "Second lesson."]

    def test_teaching_the_same_thing_twice_stores_it_once(self, store: MemoryStore):
        store.add("Prefers tabs.")
        store.add("Prefers tabs.")

        assert store.list_memories() == ["Prefers tabs."]

    def test_repeating_a_lesson_moves_it_to_the_most_recent(self, store: MemoryStore):
        # Recency decides what survives the budget, so a lesson the user just repeated
        # must not be sitting at the back of the queue waiting to be dropped.
        store.add("Old habit.")
        store.add("Something else.")
        store.add("Old habit.")

        assert store.list_memories() == ["Something else.", "Old habit."]

    def test_an_empty_memory_is_refused(self, store: MemoryStore):
        with pytest.raises(ValueError):
            store.add("   ")

    def test_a_multi_line_memory_is_flattened(self, store: MemoryStore):
        # One memory per line is the storage format; a raw newline would split this
        # into a bullet plus a line that no longer parses as a memory at all.
        store.add("Deploys on Fridays.\nNever before a release.")

        assert store.list_memories() == [
            "Deploys on Fridays. Never before a release."
        ]

    def test_every_agent_reads_the_same_list(self, store: MemoryStore):
        # Shared on purpose: a correction describes the user, not the profile that
        # happened to be listening when they gave it.
        store.add("Deploys are manual.")

        assert store.prompt_section().count("Deploys are manual.") == 1
        assert store.list_memories() == ["Deploys are manual."]


class TestEditingByHand:
    def test_the_file_is_readable_markdown(self, store: MemoryStore, tmp_path: Path):
        store.add("Prefers tabs.")

        content = (tmp_path / "memories.md").read_text()

        assert "# Memories" in content
        assert "- Prefers tabs." in content

    def test_prose_added_by_hand_is_ignored_rather_than_breaking_the_list(
        self, store: MemoryStore, tmp_path: Path
    ):
        path = tmp_path / "memories.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "# Memories\n\nSome note I typed here.\n\n- A real memory.\n## Heading\n"
        )

        assert store.list_memories() == ["A real memory."]

    def test_a_memory_deleted_by_hand_is_gone(self, store: MemoryStore, tmp_path: Path):
        store.add("First.")
        store.add("Second.")
        path = tmp_path / "memories.md"
        path.write_text(path.read_text().replace("- First.\n", ""))

        assert store.list_memories() == ["Second."]


class TestReplacing:
    def test_replacing_overwrites_the_list(self, store: MemoryStore):
        store.add("Old.")

        store.replace(["New one.", "New two."])

        assert store.list_memories() == ["New one.", "New two."]

    def test_replacing_drops_blanks_and_duplicates(self, store: MemoryStore):
        stored = store.replace(["Keep.", "  ", "Keep.", "Also keep."])

        assert stored == ["Keep.", "Also keep."]

    def test_replacing_with_nothing_forgets_everything(self, store: MemoryStore):
        store.add("Something.")

        store.replace([])

        assert store.list_memories() == []


class TestPromptBudget:
    def test_nothing_is_said_when_there_is_nothing_to_say(self, store: MemoryStore):
        assert store.prompt_section() == ""

    def test_memories_reach_the_prompt(self, store: MemoryStore):
        store.add("Prefers tabs over spaces.")

        section = store.prompt_section()

        assert "Prefers tabs over spaces." in section
        assert "learned" in section.lower()

    def test_the_oldest_memories_are_dropped_first(self):
        # Injected on every turn, so an unbounded list would quietly eat the context
        # window; the newest lessons describe the user best, so they are the keepers.
        memories = [f"Memory number {index}" for index in range(1000)]

        kept = trim_to_budget(memories)

        assert kept[-1] == "Memory number 999"
        assert len(kept) < len(memories)
        assert sum(len(entry) + 2 for entry in kept) <= MAX_MEMORY_CHARS

    def test_a_short_list_is_kept_whole(self):
        memories = ["One.", "Two.", "Three."]

        assert trim_to_budget(memories) == memories

    def test_the_stored_list_is_trimmed_as_it_grows(self, store: MemoryStore):
        for index in range(400):
            store.add(f"Lesson {index} " + "x" * 40)

        stored = store.list_memories()

        assert sum(len(entry) + 2 for entry in stored) <= MAX_MEMORY_CHARS
        assert stored[-1].startswith("Lesson 399")
