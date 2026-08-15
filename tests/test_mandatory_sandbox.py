"""Tests for the always-in-Docker conversation rules.

Every conversation runs its tools inside a container. When Docker is down that
must surface as an explicit failure at creation time -- not as an agent silently
executing on the host.
"""

from typing import Optional

import pytest
from fastapi import HTTPException

from backend.app.database import get_conversation, init_db
from backend.app.models import Conversation, ConversationStatus, CreateConversationRequest
from backend.app.agent import turns as turns_module
from backend.app.routes import conversations as conversations_route


class StubSandboxManager:
    """Test double for the Docker sandbox manager."""

    def __init__(self, running: Optional[str] = None, create_error: Optional[str] = None):
        self._running = running
        self._create_error = create_error
        self.created_for: list[str] = []
        self.ensure_calls: list[str] = []

    async def ensure_running(self, container_id: str) -> bool:
        self.ensure_calls.append(container_id)
        return container_id == self._running

    async def create_sandbox(self, conversation_id: str, workspace_dir) -> str:
        if self._create_error:
            raise RuntimeError(self._create_error)
        self.created_for.append(conversation_id)
        return f"container-for-{conversation_id[:8]}"


def _conversation(conversation_id: str, container_id: Optional[str]) -> Conversation:
    return Conversation(
        id=conversation_id,
        title="t",
        agent_id="general",
        isolated_container=True,
        container_id=container_id,
        status=ConversationStatus.ACTIVE,
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
    )


class TestEnsureSandbox:
    async def test_a_recorded_container_is_revived_not_replaced(self, monkeypatch):
        # Sub-agent conversations share their parent's container; creating a fresh
        # one keyed on the child's id would silently split them apart.
        stub = StubSandboxManager(running="parent-container")
        monkeypatch.setattr(turns_module, "sandbox_manager", stub)

        conv = _conversation("child-conv", "parent-container")
        result = await turns_module.ensure_sandbox(conv)

        assert result == "parent-container"
        assert stub.created_for == []

    async def test_a_pruned_container_is_recreated_and_persisted(self, monkeypatch):
        await init_db()
        stub = StubSandboxManager(running=None)
        monkeypatch.setattr(turns_module, "sandbox_manager", stub)

        from backend.app.database import create_conversation

        conv = await create_conversation(
            title="t", agent_id="general", isolated_container=True,
            container_id="gone-container",
        )
        result = await turns_module.ensure_sandbox(conv)

        assert result == f"container-for-{conv.id[:8]}"
        stored = await get_conversation(conv.id)
        assert stored is not None
        assert stored.container_id == result


class TestCreateConversation:
    async def test_creation_fails_loudly_when_docker_is_unavailable(self, monkeypatch):
        await init_db()
        stub = StubSandboxManager(create_error="Docker is not available")
        monkeypatch.setattr(turns_module, "sandbox_manager", stub)

        with pytest.raises(HTTPException) as excinfo:
            await conversations_route.create_new_conversation(
                CreateConversationRequest(agent_id="general")
            )

        assert excinfo.value.status_code == 503
        assert "Docker" in excinfo.value.detail

    async def test_no_half_created_conversation_survives_the_failure(self, monkeypatch):
        await init_db()
        stub = StubSandboxManager(create_error="Docker is not available")
        monkeypatch.setattr(turns_module, "sandbox_manager", stub)

        before = {c.id for c in await conversations_route.get_all_conversations()}
        with pytest.raises(HTTPException):
            await conversations_route.create_new_conversation(
                CreateConversationRequest(agent_id="general")
            )
        after = {c.id for c in await conversations_route.get_all_conversations()}

        # A conversation that cannot get its container is rolled back entirely;
        # otherwise the sidebar fills with dead entries that can never run.
        assert after == before

    async def test_a_healthy_docker_gives_the_conversation_its_container(self, monkeypatch):
        await init_db()
        stub = StubSandboxManager()
        monkeypatch.setattr(turns_module, "sandbox_manager", stub)

        conversation = await conversations_route.create_new_conversation(
            CreateConversationRequest(agent_id="general")
        )

        assert conversation.container_id == f"container-for-{conversation.id[:8]}"
        stored = await get_conversation(conversation.id)
        assert stored is not None
        assert stored.container_id == conversation.container_id
