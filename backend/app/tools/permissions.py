"""Runtime enforcement of per-agent tool permissions.

Filtering the schemas an agent is offered is a hint, not a control: a model can name
any tool it likes, including one it was never shown, and prompt injection reaching the
model through fetched web content can steer it there deliberately. Every tool call is
therefore checked against the agent profile at execution time as well.
"""

from backend.app.models import AgentConfig, ToolPermission


def resolve_tool_permission(
    agent_config: AgentConfig, tool_name: str
) -> ToolPermission:
    """Returns the effective permission for a tool under an agent profile.

    ``allowed_tools`` is an exhaustive allowlist: a tool absent from it is denied,
    and an empty list grants nothing. It deliberately does not mean "everything" --
    that reading makes revoking an agent's last tool grant it every tool instead,
    which is precisely backwards for a security control.

    Tools on the list default to auto-approval unless ``tool_permissions`` says
    otherwise.

    Args:
        agent_config: Profile of the agent making the call.
        tool_name: Name of the tool the model asked for.

    Returns:
        ToolPermission: The policy to apply to this call.
    """
    if tool_name not in agent_config.allowed_tools:
        return ToolPermission.DENIED

    permission = agent_config.tool_permissions.get(tool_name)
    if permission is None:
        return ToolPermission.AUTO_APPROVE
    if isinstance(permission, ToolPermission):
        return permission
    try:
        return ToolPermission(str(permission))
    except ValueError:
        return ToolPermission.AUTO_APPROVE


def format_denial(tool_name: str, agent_config: AgentConfig) -> str:
    """Builds the tool result returned to the model when a call is refused."""
    return (
        f"Error: Tool '{tool_name}' is not permitted for agent"
        f" '{agent_config.id}'. Do not attempt to call it again in this"
        " conversation; use one of your permitted tools instead."
    )
