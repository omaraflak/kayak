"""Tests that Kayak recovers on its own from the states that used to leave it broken.

Each case here was a way the app could end up needing someone who knows its insides:
a database or settings file it could not read, a stream that went quiet for good, a
step shown as running forever, an upload that took the server down, a list of
memories overwritten from a stale copy. The people Kayak is for cannot open a
terminal to fix any of those.
"""

import asyncio
import errno
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List

import aiosqlite
import pytest
from fastapi.testclient import TestClient

from backend.app import sse
from backend.app.agent import engine as engine_module
from backend.app.agent import turns
from backend.app.agent.engine import _build_llm_messages, agent_engine
from backend.app.config import Settings, settings
from backend.app.database import (
    add_message,
    create_conversation,
    get_messages,
    init_db,
)
from backend.app.main import app
from backend.app.memories.store import MemoryStore
from backend.app.models import AgentConfig, MessageRole
from backend.app.realtime import ConversationEventHub
from backend.app.tools.registry import tool_registry


@pytest.fixture
async def db(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "DB_PATH", tmp_path / "kayak.db")
    monkeypatch.setattr(settings, "WORKSPACES_DIR", tmp_path / "workspaces")
    await init_db()
    return tmp_path


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


class TestDatabase:
    async def test_a_database_from_an_older_kayak_gains_the_new_column(
        self, tmp_path: Path, monkeypatch
    ):
        path = tmp_path / "old.db"
        monkeypatch.setattr(settings, "DB_PATH", path)
        monkeypatch.setattr(settings, "WORKSPACES_DIR", tmp_path / "workspaces")
        async with aiosqlite.connect(path) as old:
            await old.execute(
                "CREATE TABLE messages (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,"
                " role TEXT NOT NULL, content TEXT, thinking TEXT, tool_calls TEXT,"
                " tool_call_id TEXT, name TEXT, created_at TEXT NOT NULL)"
            )
            await old.commit()

        await init_db()
        conversation = await create_conversation(title="t")
        await add_message(conversation.id, MessageRole.TOOL, content="Error: x", is_error=True)

        assert (await get_messages(conversation.id))[0].is_error

    async def test_a_damaged_database_is_set_aside_and_kayak_still_starts(
        self, tmp_path: Path, monkeypatch
    ):
        path = tmp_path / "kayak.db"
        path.write_bytes(b"this is not a database" * 100)
        monkeypatch.setattr(settings, "DB_PATH", path)
        monkeypatch.setattr(settings, "WORKSPACES_DIR", tmp_path / "workspaces")

        await init_db()

        assert any(item.name.startswith("kayak.db.damaged-") for item in tmp_path.iterdir())
        assert await create_conversation(title="fresh start")


    async def test_a_full_disk_is_not_mistaken_for_a_damaged_database(
        self, tmp_path: Path, monkeypatch
    ):
        import sqlite3

        from backend.app import database

        path = tmp_path / "kayak.db"
        monkeypatch.setattr(settings, "DB_PATH", path)
        monkeypatch.setattr(settings, "WORKSPACES_DIR", tmp_path / "workspaces")
        await init_db()
        await create_conversation(title="keep me")

        async def full_disk():
            raise sqlite3.OperationalError("database or disk is full")

        monkeypatch.setattr(database, "_create_schema", full_disk)

        with pytest.raises(sqlite3.OperationalError):
            await init_db()
        # The history is still where it was, not set aside as damaged.
        assert path.exists()
        assert not any(".damaged-" in item.name for item in tmp_path.iterdir())


class TestSettingsFile:
    def test_an_unreadable_file_is_moved_aside_not_overwritten_later(self, tmp_path: Path):
        broken = tmp_path / "settings.json"
        broken.write_text("{ not json", encoding="utf-8")
        loaded = Settings.__new__(Settings)
        loaded.SETTINGS_FILE = broken

        loaded.load_from_file()

        assert not broken.exists()
        kept = [item for item in tmp_path.iterdir() if ".unreadable-" in item.name]
        assert kept and kept[0].read_text(encoding="utf-8") == "{ not json"

    def test_a_pasted_key_loses_its_invisible_whitespace(
        self, client: TestClient, tmp_path: Path, monkeypatch
    ):
        monkeypatch.setattr(settings, "SETTINGS_FILE", tmp_path / "settings.json")
        monkeypatch.setattr(settings, "OPENAI_API_KEY", "")

        response = client.post("/api/settings", json={"OPENAI_API_KEY": "  sk-abc123\n"})

        assert response.status_code == 200
        assert settings.OPENAI_API_KEY == "sk-abc123"


class TestCustomTools:
    def test_one_unreadable_tool_does_not_stop_the_others_loading(
        self, tmp_path: Path, monkeypatch
    ):
        monkeypatch.setattr(settings, "TOOLS_DIR", tmp_path)
        good = tmp_path / "good_tool"
        good.mkdir()
        (good / "tool.py").write_text(
            'def good_tool(text: str) -> str:\n    """Echoes."""\n    return text\n',
            encoding="utf-8",
        )
        bad = tmp_path / "bad_tool"
        bad.mkdir()
        (bad / "tool.py").write_text(
            'def bad_tool(text: str) -> str:\n    """Echoes."""\n    return text\n',
            encoding="utf-8",
        )
        (bad / "verify.py").write_bytes(b"\xff\xfe not utf-8 \x80")

        try:
            tool_registry.load_custom_tools()
            assert tool_registry.is_custom("good_tool")
            assert not tool_registry.has_tool("bad_tool")
        finally:
            monkeypatch.undo()
            tool_registry.load_custom_tools()

    def test_a_custom_tool_can_be_deleted(self, client: TestClient, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(settings, "TOOLS_DIR", tmp_path)
        folder = tmp_path / "doomed_tool"
        folder.mkdir()
        (folder / "tool.py").write_text(
            'def doomed_tool(text: str) -> str:\n    """Echoes."""\n    return text\n',
            encoding="utf-8",
        )
        try:
            tool_registry.load_custom_tools()

            response = client.delete("/api/tools/doomed_tool")

            assert response.status_code == 200
            assert not folder.exists()
        finally:
            monkeypatch.undo()
            tool_registry.load_custom_tools()

    def test_a_built_in_tool_cannot_be(self, client: TestClient):
        response = client.delete("/api/tools/run_command")

        assert response.status_code == 400
        assert "can't be deleted" in response.json()["detail"]


class TestWorkspaceFiles:
    async def test_a_file_with_a_non_latin_name_can_be_opened(self, client, db: Path):
        conversation = await create_conversation(title="t")
        workspace = db / "workspaces" / conversation.id
        name = "Capture d’écran 10.00.00 AM 😀.png"
        (workspace / name).write_bytes(b"png")

        response = client.get(
            f"/api/conversations/{conversation.id}/fs/file", params={"path": name}
        )

        assert response.status_code == 200
        assert response.content == b"png"

    async def test_an_upload_is_written_to_the_workspace(self, client, db: Path):
        conversation = await create_conversation(title="t")

        response = client.post(
            f"/api/conversations/{conversation.id}/fs/upload",
            files=[("files", ("folder/notes.txt", b"hello" * 10000, "text/plain"))],
        )

        assert response.status_code == 200
        saved = db / "workspaces" / conversation.id / "folder" / "notes.txt"
        assert saved.read_bytes() == b"hello" * 10000

    async def test_a_full_disk_is_explained_and_leaves_no_partial_file(
        self, client, db: Path, monkeypatch
    ):
        conversation = await create_conversation(title="t")

        def full_disk(source, destination, length=0):
            destination.write(b"half")
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr("backend.app.routes.workspace.shutil.copyfileobj", full_disk)

        response = client.post(
            f"/api/conversations/{conversation.id}/fs/upload",
            files=[("files", ("big.bin", b"x" * 100, "application/octet-stream"))],
        )

        assert response.status_code == 507
        assert "out of disk space" in response.json()["detail"]
        assert not (db / "workspaces" / conversation.id / "big.bin").exists()


class TestMemories:
    def test_editing_one_memory_keeps_those_added_since_the_page_loaded(
        self, tmp_path: Path, monkeypatch
    ):
        monkeypatch.setattr(settings, "MEMORY_FILE", tmp_path / "memories.md")
        store = MemoryStore()
        store.add("Prefers tabs.")
        # An agent learns something while the Memories page is open.
        store.add("Lives in Paris.")

        store.edit("Prefers tabs.", "Prefers spaces.")

        assert store.list_memories() == ["Prefers spaces.", "Lives in Paris."]

    def test_an_empty_replacement_forgets_the_memory(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(settings, "MEMORY_FILE", tmp_path / "memories.md")
        store = MemoryStore()
        store.add("Prefers tabs.")
        store.add("Lives in Paris.")

        store.edit("Prefers tabs.", "  ")

        assert store.list_memories() == ["Lives in Paris."]

    def test_editing_a_memory_that_changed_meanwhile_is_refused(
        self, client: TestClient, tmp_path: Path, monkeypatch
    ):
        monkeypatch.setattr(settings, "MEMORY_FILE", tmp_path / "memories.md")
        MemoryStore().add("Prefers tabs.")

        response = client.patch(
            "/api/memories", json={"previous": "Gone already.", "replacement": "x"}
        )

        assert response.status_code == 409
        assert MemoryStore().list_memories() == ["Prefers tabs."]


class TestStreams:
    async def test_a_listener_that_fell_behind_has_its_stream_ended(self):
        hub = ConversationEventHub()
        queue = hub.subscribe("c")
        for index in range(2000):
            hub.publish("c", {"type": "token", "content": str(index)})

        class Connected:
            async def is_disconnected(self) -> bool:
                return False

        frames = [frame async for frame in sse.drain(Connected(), queue, {"type": "ping"})]

        # Ending is what makes the browser reconnect and be caught up; a stream that
        # stayed open after being dropped never delivered anything again.
        assert frames == []

    def test_a_turn_records_where_it_began(self):
        hub = ConversationEventHub()

        hub.begin_turn("c", "message-7")

        assert hub.replay_for("c").anchor_message_id == "message-7"


def _agent(monkeypatch) -> AgentConfig:
    agent = AgentConfig(
        id="tester",
        name="Tester",
        description="test",
        model="test/model",
        allowed_tools=["run_command"],
        tool_permissions={},
    )
    monkeypatch.setattr(engine_module.agent_manager, "get_agent", lambda agent_id: agent)
    return agent


def _stub_turns(monkeypatch, turns_to_play: List[List[Dict[str, Any]]]) -> None:
    remaining = list(turns_to_play)

    async def fake_stream(**kwargs: Any) -> AsyncGenerator[Dict[str, Any], None]:
        for chunk in remaining.pop(0) if remaining else [{"type": "token", "content": "ok"}]:
            yield chunk

    monkeypatch.setattr(engine_module, "generate_completion_stream", fake_stream)


class TestTranscript:
    async def test_a_failed_step_is_stored_as_failed(self, db, monkeypatch):
        _agent(monkeypatch)
        monkeypatch.setattr(
            engine_module.tool_registry, "get_tool_definitions", lambda allowed_names=None: []
        )

        async def failing_tool(name, arguments, context):
            return "Error (1): command not found"

        monkeypatch.setattr(engine_module.tool_registry, "execute_tool", failing_tool)
        _stub_turns(
            monkeypatch,
            [
                [{"type": "tool_call_delta", "index": 0, "id": "call_1", "name": "run_command", "arguments": "{}"}],
                [{"type": "token", "content": "done"}],
            ],
        )
        conversation = await create_conversation(title="t", agent_id="tester")
        await add_message(conversation.id, MessageRole.USER, content="go")

        [event async for event in agent_engine.run(conversation.id)]

        results = [m for m in await get_messages(conversation.id) if m.role == MessageRole.TOOL]
        # "Error (1):" did not start with "Error:", so a reload showed it completed.
        assert results and results[0].is_error

    async def test_an_explained_failure_is_never_sent_to_the_model(self, db, monkeypatch):
        agent = _agent(monkeypatch)
        conversation = await create_conversation(title="t", agent_id="tester")
        await add_message(conversation.id, MessageRole.USER, content="hello")
        await add_message(
            conversation.id,
            MessageRole.ASSISTANT,
            content="OpenAI didn't accept your key.",
            is_error=True,
        )
        await add_message(conversation.id, MessageRole.USER, content="try again")
        session = engine_module._SessionContext(
            conversation_id=conversation.id,
            conversation=conversation,
            agent_config=agent,
            workspace_dir=db,
            container_id=None,
            runtime_context={},
            tool_schemas=None,
        )

        sent = _build_llm_messages(await get_messages(conversation.id), session)

        assert all("didn't accept your key" not in str(entry.get("content")) for entry in sent)


class TestTurns:
    async def test_a_replaced_run_does_not_unregister_its_replacement(self, monkeypatch):
        release = asyncio.Event()

        async def slow_get_conversation(conversation_id):
            await release.wait()
            return None  # ends the run, through its `finally`

        monkeypatch.setattr(turns, "get_conversation", slow_get_conversation)
        turns.start("c", "a")
        first = turns._running_turns["c"]
        await asyncio.sleep(0)
        # A newer run has taken the slot by the time the first one finishes.
        newer = asyncio.get_running_loop().create_future()
        turns._running_turns["c"] = newer  # type: ignore[assignment]
        try:
            release.set()
            await first

            # Removing it would leave Stop unable to find the run still going.
            assert turns._running_turns.get("c") is newer
        finally:
            turns._running_turns.pop("c", None)
            newer.cancel()

    async def test_a_turn_that_cannot_start_still_ends(self, db, monkeypatch):
        conversation = await create_conversation(title="t", agent_id="tester")
        published: List[str] = []

        async def no_docker(_conversation):
            raise RuntimeError("Docker is not running. Start Docker Desktop and try again.")

        monkeypatch.setattr(turns, "ensure_sandbox", no_docker)
        monkeypatch.setattr(
            turns.conversation_events,
            "publish",
            lambda conversation_id, event: published.append(event["type"]),
        )

        turns.start(conversation.id, "tester")
        await turns._running_turns[conversation.id]

        # The page releases its composer, and resets what it tracks for a turn, on
        # "done"; a failure that only said "error" left both behind.
        assert published[-2:] == ["error", "done"]
        stored = await get_messages(conversation.id)
        assert stored[-1].is_error and "Docker Desktop" in (stored[-1].content or "")

    async def test_sends_into_one_conversation_take_turns(self):
        order: List[str] = []

        async def send(label: str) -> None:
            async with turns.turn_lock("same"):
                order.append(f"{label}-start")
                await asyncio.sleep(0.01)
                order.append(f"{label}-end")

        await asyncio.gather(send("a"), send("b"))

        assert order in (
            ["a-start", "a-end", "b-start", "b-end"],
            ["b-start", "b-end", "a-start", "a-end"],
        )
        turns.forget("same")
