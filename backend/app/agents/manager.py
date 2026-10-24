"""Agent profiles, one YAML file each in data/agents.

The default profiles ship as files in data/agents, copied into a new install's data
directory, so there is exactly one definition of each and deleting one sticks.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional
import yaml
from backend.app.config import settings
from backend.app.user_errors import UserFacingError
from backend.app.fs_cache import DirectorySignature, directory_signature
from backend.app.identifiers import clean_identifier
from backend.app.models import AgentConfig

logger = logging.getLogger(__name__)


def allowed_subagent_ids(agent: AgentConfig) -> List[str]:
    """Returns the agent profile ids this agent may start as sub-agents.

    An unset list means the agent may only delegate to its own profile. Anything
    wider must be granted explicitly: otherwise a restricted agent could simply
    spawn a more permissive profile and act through it.
    """
    if agent.allowed_subagents is None:
        return [agent.id]
    return list(agent.allowed_subagents)


def _load_agent_file(file_path: Path) -> Optional[AgentConfig]:
    """Parses a YAML agent configuration file."""
    try:
        data = yaml.safe_load(file_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return AgentConfig(**data)
    except Exception:
        logger.exception("Could not load agent from %s", file_path.name)
    return None


class AgentManager:
    """Manages custom and built-in AgentConfig profiles stored as YAML files."""

    def __init__(self):
        self._agents: Dict[str, AgentConfig] = {}
        self._signature: DirectorySignature = ()
        self._loaded: bool = False

    def _current_signature(self) -> DirectorySignature:
        """Fingerprints the agents directory to detect edits made outside the app."""
        return directory_signature(settings.AGENTS_DIR, patterns=("*.yaml", "*.yml"))

    def _refresh_if_stale(self) -> None:
        """Reparses agent files only when the directory has actually changed."""
        signature = self._current_signature()
        if self._loaded and signature == self._signature:
            return
        self.load_all_agents()

    def load_all_agents(self):
        """Scans the agents directory and reloads all YAML configurations."""
        self._agents.clear()
        agents_dir = settings.AGENTS_DIR
        self._loaded = True
        self._signature = self._current_signature()
        if not agents_dir.exists():
            return

        for file_path in sorted(agents_dir.iterdir()):
            if file_path.suffix in [".yaml", ".yml"]:
                agent = _load_agent_file(file_path)
                if agent:
                    self._agents[agent.id] = agent

    def list_agents(self) -> List[AgentConfig]:
        self._refresh_if_stale()
        return list(self._agents.values())

    def get_agent(self, agent_id: str) -> Optional[AgentConfig]:
        self._refresh_if_stale()
        return self._agents.get(agent_id)

    def save_agent(self, agent: AgentConfig):
        clean_id = clean_identifier(agent.id)
        if not clean_id:
            # An empty id was written to a file named ".yaml".
            raise UserFacingError("Give the agent a name first.")
        agent.id = clean_id
        file_path = settings.AGENTS_DIR / f"{clean_id}.yaml"

        data = agent.model_dump(mode="json")
        file_path.write_text(
            yaml.dump(data, sort_keys=False), encoding="utf-8"
        )
        self._agents[clean_id] = agent
        self._signature = self._current_signature()

    def delete_agent(self, agent_id: str) -> bool:
        file_path = settings.AGENTS_DIR / f"{agent_id}.yaml"
        if file_path.exists():
            file_path.unlink()
            self._agents.pop(agent_id, None)
            self._signature = self._current_signature()
            return True
        return False


agent_manager = AgentManager()
