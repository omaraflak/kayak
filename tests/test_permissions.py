"""Tests for runtime tool permission resolution.

Schema filtering only controls what a model is offered. A model can still name a tool
it was never shown -- including because injected web content told it to -- so the
policy has to hold at execution time.
"""

import pytest
from backend.app.models import AgentConfig, ToolPermission
from backend.app.tools.permissions import format_denial, resolve_tool_permission


def _agent(**overrides) -> AgentConfig:
    defaults = dict(
        id="tester",
        name="Tester",
        description="test agent",
        allowed_tools=[],
        tool_permissions={},
    )
    defaults.update(overrides)
    return AgentConfig(**defaults)


class TestResolveToolPermission:
    def test_empty_allowlist_permits_nothing(self):
        # Emptying the list must revoke every tool. Reading it as "unrestricted"
        # would make turning an agent's last tool off grant it all of them.
        agent = _agent()
        assert resolve_tool_permission(agent, "run_command") == ToolPermission.DENIED

    def test_tool_absent_from_allowlist_is_denied(self):
        agent = _agent(allowed_tools=["read_file"])
        assert resolve_tool_permission(agent, "run_command") == ToolPermission.DENIED

    def test_tool_on_allowlist_defaults_to_auto_approve(self):
        agent = _agent(allowed_tools=["read_file"])
        assert resolve_tool_permission(agent, "read_file") == ToolPermission.AUTO_APPROVE

    def test_explicit_ask_user_is_honoured(self):
        agent = _agent(
            allowed_tools=["run_command"],
            tool_permissions={"run_command": ToolPermission.ASK_USER},
        )
        assert resolve_tool_permission(agent, "run_command") == ToolPermission.ASK_USER

    def test_explicit_denial_overrides_allowlist_membership(self):
        agent = _agent(
            allowed_tools=["run_command"],
            tool_permissions={"run_command": ToolPermission.DENIED},
        )
        assert resolve_tool_permission(agent, "run_command") == ToolPermission.DENIED

    def test_string_values_from_yaml_are_coerced(self):
        # Agent profiles are authored as YAML, so permissions arrive as plain strings.
        agent = _agent(
            allowed_tools=["run_command"], tool_permissions={"run_command": "ask_user"}
        )
        assert resolve_tool_permission(agent, "run_command") == ToolPermission.ASK_USER

    def test_unknown_tool_not_on_allowlist_is_denied(self):
        agent = _agent(allowed_tools=["read_file"])
        assert resolve_tool_permission(agent, "definitely_not_a_tool") == ToolPermission.DENIED


class TestFormatDenial:
    def test_denial_message_names_the_tool_and_agent(self):
        message = format_denial("run_command", _agent(id="general"))
        assert "run_command" in message
        assert "general" in message
        assert message.startswith("Error:")


class TestAgentConfigSerialization:
    def test_permissions_round_trip_as_plain_strings(self):
        # Agent profiles are persisted with yaml.dump; an enum instance would be
        # written as a Python object tag that never loads back cleanly.
        agent = _agent(tool_permissions={"run_command": ToolPermission.ASK_USER})
        dumped = agent.model_dump(mode="json")

        assert dumped["tool_permissions"] == {"run_command": "ask_user"}
        assert isinstance(dumped["tool_permissions"]["run_command"], str)


@pytest.mark.parametrize("permission", list(ToolPermission))
def test_every_permission_value_is_resolvable(permission: ToolPermission):
    agent = _agent(allowed_tools=["tool_x"], tool_permissions={"tool_x": permission})
    assert resolve_tool_permission(agent, "tool_x") == permission
