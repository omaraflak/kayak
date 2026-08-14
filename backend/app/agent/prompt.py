from pathlib import Path
from typing import List, Optional
from backend.app.agents.manager import agent_manager, allowed_subagent_ids
from backend.app.models import AgentConfig
from backend.app.skills.registry import skill_registry


def build_system_prompt(
    agent_config: AgentConfig,
    workspace_dir: Optional[Path] = None,
    container_id: Optional[str] = None,
) -> str:
    """Constructs the comprehensive system prompt for the agent turn."""
    parts = []

    # 1. Base Agent Persona & System Prompt
    parts.append(agent_config.system_prompt.strip())

    # 2. Environment & Execution Context. These facts exist because their absence
    # was expensive: an agent once spent a third of its step budget scanning the
    # filesystem for uploads, timing out apt-get, and re-loading data in fresh
    # processes -- all avoidable with the lines below.
    allowed_tools = set(agent_config.allowed_tools)
    parts.append("\n## Environment Context")
    if container_id:
        parts.append(
            f"- Sandbox: Isolated Docker Container `{container_id[:12]}`"
        )
        parts.append(
            "- Workspace: Mounted at `/workspace` with full root shell execution permissions."
        )
        parts.append(
            "- Files the user uploads are in the workspace root. Look there first"
            + (" (`list_directory` or `find_files`)." if "find_files" in allowed_tools else ".")
        )
        parts.append(
            "- Missing Python packages: install with `pip install <packages>` (one call,"
            " all packages). Do not use apt-get; it is slow enough to time out."
        )
    else:
        ws_str = str(workspace_dir.resolve()) if workspace_dir else "Local"
        parts.append(f"- Workspace Directory: `{ws_str}`")
        parts.append(
            "- Mode: Host execution mode. Relative file paths are resolved relative to this workspace directory."
        )
    if "run_command" in allowed_tools:
        parts.append(
            "- Every `run_command` call is a fresh shell, but the working directory"
            " persists: `cd project` carries into your next command. Environment"
            " variables do not persist, only files do. The default timeout is 60s;"
            " pass `timeout` (up to 600) for slower commands."
        )
    if "run_python" in allowed_tools:
        parts.append(
            "- `run_python` is one persistent Python session: variables, imports, and"
            " loaded data stay alive across calls. Use it for iterative work instead"
            " of `python3 -c` one-liners, and load expensive data only once."
        )

    # 3. Preloaded Skills (Full instructions)
    if agent_config.preloaded_skills:
        parts.append("\n## Preloaded Active Skills")
        for skill_name in agent_config.preloaded_skills:
            skill = skill_registry.get_skill(skill_name)
            if skill:
                parts.append(f"### Skill: {skill.name}")
                parts.append(f"{skill.instructions.strip()}\n")

    # 4. Available Skills Index (On-demand via load_skill)
    all_skills = skill_registry.list_skills()
    available_on_demand = [
        s
        for s in all_skills
        if s.name not in agent_config.preloaded_skills
        and (not agent_config.allowed_skills or s.name in agent_config.allowed_skills)
    ]

    if available_on_demand:
        parts.append("\n## Available Skills (Load on-demand)")
        parts.append(
            "The following specialized skills are available. Call `load_skill(skill_name)` before executing complex workflows in these domains:"
        )
        for s in available_on_demand:
            parts.append(f"- **{s.name}**: {s.description}")

    # 5. Sub-agent profiles this agent is allowed to start. Served from the agent's
    # own policy rather than listing every profile: advertising an agent it cannot
    # start would only produce failed calls.
    if "spawn_subagent" in agent_config.allowed_tools:
        parts.append("\n## Sub-agent Profiles")
        profiles = [
            profile
            for profile in (
                agent_manager.get_agent(allowed_id)
                for allowed_id in allowed_subagent_ids(agent_config)
            )
            if profile
        ]
        if profiles:
            parts.append(
                "You may start sub-agents with these profiles only (pass the id to `spawn_subagent`):"
            )
            for profile in profiles:
                parts.append(f"- **{profile.id}**: {profile.name} — {profile.description}")
        else:
            parts.append(
                "No sub-agent profiles are available to you; do not call `spawn_subagent`."
            )

    # 6. Background Tasks & Sub-Agent Guidance
    parts.append("\n## Execution & Tool Guidelines")
    parts.append(
        "- **File Modifications**: Always inspect existing files using `read_file` before calling `edit_file` or `write_file`. Make minimal, precise replacements."
    )
    parts.append(
        "- **Long-running Jobs**: If a command is long-running (e.g. running a test suite, building assets, starting a server), use `start_background_task` rather than blocking the turn with a high timeout."
    )
    parts.append(
        "- **Delegation**: For complex multi-part investigations or tasks that benefit from separate context, use `spawn_subagent`."
    )

    return "\n".join(parts)
