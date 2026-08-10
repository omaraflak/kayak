from typing import Dict, List
from fastapi import APIRouter, HTTPException
from backend.app.agents.manager import agent_manager
from backend.app.models import AgentConfig

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("", response_model=List[AgentConfig])
async def list_all_agents() -> List[AgentConfig]:
    """Lists all configured agent profiles.

    Returns:
        A list of AgentConfig objects.
    """
    return agent_manager.list_agents()


@router.get("/{agent_id}", response_model=AgentConfig)
async def get_agent_config(agent_id: str) -> AgentConfig:
    """Retrieves an agent configuration profile by identifier.

    Args:
        agent_id: Unique agent identifier.

    Returns:
        The AgentConfig record.

    Raises:
        HTTPException: If the agent profile is not found.
    """
    agent = agent_manager.get_agent(agent_id)
    if not agent:
        raise HTTPException(
            status_code=404, detail=f"Agent '{agent_id}' not found"
        )
    return agent


@router.post("", response_model=AgentConfig)
async def save_agent_config(agent: AgentConfig) -> AgentConfig:
    """Creates or updates an agent configuration profile YAML file.

    Args:
        agent: AgentConfig configuration to persist.

    Returns:
        The persisted AgentConfig object.
    """
    agent_manager.save_agent(agent)
    return agent


@router.delete("/{agent_id}")
async def delete_agent_config(agent_id: str) -> Dict[str, str]:
    """Deletes a custom agent profile.

    Args:
        agent_id: Unique agent identifier to delete.

    Returns:
        Status response dictionary.

    Raises:
        HTTPException: If the agent profile could not be found or deleted.
    """
    success = agent_manager.delete_agent(agent_id)
    if not success:
        raise HTTPException(
            status_code=404, detail=f"Agent '{agent_id}' not found"
        )
    return {"status": "deleted"}
