"""Tests for the sub-agent allowlist and container sharing.

Delegation must not be an escape hatch: an agent restricted to safe tools could
otherwise spawn a more permissive profile and act through it. By default an agent
may therefore only start sub-agents with its own profile, and a sub-agent always
runs in the same container as its parent.
"""

from typing import Any, Dict

import pytest

from backend.app.agents.manager import agent_manager, allowed_subagent_ids
from backend.app.database import get_conversation, init_db
from backend.app.models import AgentConfig
from backend.app.tools.builtins import subagent_tools
from backend.app.tools.builtins.subagent_tools import spawn_subagent


def _agent(agent_id: str, allowed_subagents=None) -> AgentConfig:
    return AgentConfig(
        id=agent_id,
        name=agent_id.title(),
        description=f"Test profile {agent_id}",
        allowed_subagents=allowed_subagents,
    )


@pytest.fixture
def profiles():
    """Registers throwaway agent profiles and cleans them up afterwards."""
    created = ["locked_down", "powerful", "delegator"]
    agent_manager.save_agent(_agent("locked_down"))
    agent_manager.save_agent(_agent("powerful"))
    agent_manager.save_agent(_agent("delegator", allowed_subagents=["powerful"]))
    yield
    for agent_id in created:
        agent_manager.delete_agent(agent_id)


class TestAllowedSubagentIds:
    def test_defaults_to_only_the_agents_own_profile(self):
        assert allowed_subagent_ids(_agent("solo")) == ["solo"]

    def test_an_explicit_grant_is_taken_as_is(self):
        agent = _agent("lead", allowed_subagents=["helper", "researcher"])
        assert allowed_subagent_ids(agent) == ["helper", "researcher"]

    def test_an_empty_grant_means_no_delegation_at_all(self):
        # [] is a deliberate choice, distinct from the unset default.
        assert allowed_subagent_ids(_agent("hermit", allowed_subagents=[])) == []


class TestSpawnEnforcement:
    async def test_a_restricted_agent_cannot_start_another_profile(self, profiles):
        result = await spawn_subagent(
            agent_id="powerful",
            prompt="do everything",
            conversation_id="conv-1",
            caller_agent_id="locked_down",
        )
        assert "not allowed to start 'powerful'" in result
        assert "locked_down" in result

    async def test_an_explicit_grant_does_not_include_the_agent_itself(self, profiles):
        # 'delegator' may start 'powerful' but was not granted its own profile.
        result = await spawn_subagent(
            agent_id="delegator",
            prompt="recurse",
            conversation_id="conv-1",
            caller_agent_id="delegator",
        )
        assert "not allowed to start 'delegator'" in result

    async def test_a_missing_profile_is_reported_before_anything_is_created(self):
        result = await spawn_subagent(
            agent_id="does_not_exist",
            prompt="hello",
            conversation_id="conv-1",
            caller_agent_id=None,
        )
        assert result == "Error: Agent profile 'does_not_exist' does not exist."

    async def test_own_profile_is_allowed_and_the_parent_container_is_shared(
        self, profiles, monkeypatch
    ):
        await init_db()

        # The spawned task rows carry a foreign key to the parent conversation, so
        # it has to genuinely exist.
        from backend.app.database import create_conversation

        parent = await create_conversation(title="parent", agent_id="locked_down")

        captured: Dict[str, Any] = {}

        async def fake_loop(**kwargs):
            captured.update(kwargs)
            return "done"

        monkeypatch.setattr(subagent_tools, "_run_subagent_loop", fake_loop)

        result = await spawn_subagent(
            agent_id="locked_down",
            prompt="focused sub-task",
            conversation_id=parent.id,
            container_id="parent-container-123",
            caller_agent_id="locked_down",
        )

        assert "Finished" in result
        # The child runs in the parent's container, not one of its own.
        assert captured["container_id"] == "parent-container-123"

        # And that binding is persisted, so reopening the child later still uses it.
        child = await get_conversation(captured["child_conv_id"])
        assert child is not None
        assert child.container_id == "parent-container-123"
