from pathlib import Path
import re
from typing import Dict, List, Optional
import yaml
from backend.app.config import settings
from backend.app.fs_cache import DirectorySignature, directory_signature
from backend.app.models import AgentConfig

DEFAULT_AGENTS: List[AgentConfig] = [
    AgentConfig(
        id="general",
        name="General Assistant",
        description="Friendly, all-purpose assistant for questions, brainstorming, and everyday tasks.",
        model="gemini/gemini-3.6-flash",
        temperature=0.7,
        system_prompt="You are Kayak, an intelligent, helpful, and concise AI assistant. Answer clearly and format code snippets in Markdown.",
        allowed_tools=["web_search", "fetch_url", "load_skill", "remember"],
        allowed_skills=[],
        preloaded_skills=[],
        tool_permissions={},
    ),
    AgentConfig(
        id="coding",
        name="Coding Engineer",
        description="Autonomous software engineering agent capable of reading, writing, editing code, running commands, and monitoring background tasks.",
        model="gemini/gemini-3.6-flash",
        temperature=0.2,
        system_prompt=(
            "You are Kayak's Autonomous Coding Engineer. You have full access to workspace file operations, command line tools, and background task management.\n"
            "Work efficiently -- tool calls are a budget: locate inputs with list_directory or find_files, explore and prototype in run_python where state persists between calls, "
            "and write the final deliverable as a script file early, then iterate on it with edit_file.\n"
            "Always inspect directory structure and files before making edits. Use precise search-and-replace for file edits. Test your changes by running commands or automated test suites.\n"
            "If a task is long-running (such as starting a server or long build), use start_background_task and monitor it."
        ),
        allowed_tools=[
            "read_file",
            "write_file",
            "edit_file",
            "list_directory",
            "find_files",
            "run_command",
            "run_python",
            "start_background_task",
            "get_task_status",
            "send_task_input",
            "stop_task",
            "spawn_subagent",
            "get_subagent_result",
            "load_skill",
            "web_search",
            "remember",
        ],
        allowed_skills=["coding_best_practices", "test_driven_development"],
        preloaded_skills=["coding_best_practices"],
        tool_permissions={"run_command": "auto_approve", "run_python": "auto_approve"},
    ),
    AgentConfig(
        id="tool_architect",
        name="Tool Architect",
        description="Specialized agent powered by the 'tool_creator' skill, dedicated to designing, coding, testing, and activating new Python tools in Kayak.",
        model="gemini/gemini-3.6-flash",
        temperature=0.1,
        system_prompt=(
            "You are Kayak's Tool Architect. Your role is to design clean, robust, opinionated tools with auto-extractable JSON schemas and automated verify.py test suites.\n"
            "Follow the preloaded tool_creator skill instructions. When drafting tools, test them thoroughly using `verify_tool` before calling `activate_tool`."
        ),
        allowed_tools=[
            "verify_tool",
            "activate_tool",
            "get_tool_source",
            "read_file",
            "write_file",
            "edit_file",
            "list_directory",
            "run_command",
            "load_skill",
            "web_search",
            "remember",
        ],
        allowed_skills=["tool_creator"],
        preloaded_skills=["tool_creator"],
        tool_permissions={},
    ),
    AgentConfig(
        id="skill_architect",
        name="Skill Architect",
        description="Specialized agent powered by the 'skill_creator' skill, dedicated to crafting, refining, and publishing markdown skills.",
        model="gemini/gemini-3.6-flash",
        temperature=0.3,
        system_prompt=(
            "You are Kayak's Skill Architect. You craft actionable, concise, and well-structured markdown skills for other agents in the platform.\n"
            "Follow the preloaded skill_creator skill. Use `create_or_update_skill` to save completed skills to disk."
        ),
        allowed_tools=[
            "create_or_update_skill",
            "list_available_skills",
            "load_skill",
            "read_file",
            "write_file",
            "web_search",
            "fetch_url",
            "remember",
        ],
        allowed_skills=["skill_creator"],
        preloaded_skills=["skill_creator"],
        tool_permissions={},
    ),
]


def allowed_subagent_ids(agent: AgentConfig) -> List[str]:
    """Returns the agent profile ids this agent may start as sub-agents.

    An unset list means the agent may only delegate to its own profile. Anything
    wider must be granted explicitly: otherwise a restricted agent could simply
    spawn a more permissive profile and act through it.
    """
    if agent.allowed_subagents is None:
        return [agent.id]
    return list(agent.allowed_subagents)


def _clean_agent_id(raw_id: str) -> str:
    """Sanitizes an agent identifier to safe filesystem/URL characters."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", raw_id.lower().strip())


def _load_agent_file(file_path: Path) -> Optional[AgentConfig]:
    """Parses a YAML agent configuration file."""
    try:
        data = yaml.safe_load(file_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return AgentConfig(**data)
    except Exception as e:
        print(f"Error loading agent from '{file_path.name}': {e}")
    return None


class AgentManager:
    """Manages custom and built-in AgentConfig profiles stored as YAML files."""

    def __init__(self):
        self._agents: Dict[str, AgentConfig] = {}
        self._signature: DirectorySignature = ()
        self._loaded: bool = False
        self.ensure_default_agents()

    def _current_signature(self) -> DirectorySignature:
        """Fingerprints the agents directory to detect edits made outside the app."""
        return directory_signature(settings.AGENTS_DIR, patterns=("*.yaml", "*.yml"))

    def _refresh_if_stale(self) -> None:
        """Reparses agent files only when the directory has actually changed."""
        signature = self._current_signature()
        if self._loaded and signature == self._signature:
            return
        self.load_all_agents()

    def ensure_default_agents(self):
        """Ensures default YAML agent configurations exist on disk."""
        agents_dir = settings.AGENTS_DIR
        agents_dir.mkdir(parents=True, exist_ok=True)

        for default_agent in DEFAULT_AGENTS:
            file_path = agents_dir / f"{default_agent.id}.yaml"
            if not file_path.exists():
                self.save_agent(default_agent)

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
        clean_id = _clean_agent_id(agent.id)
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
