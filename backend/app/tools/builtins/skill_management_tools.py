from backend.app.skills.registry import skill_registry
from backend.app.models import ToolCategory, ToolRisk
from backend.app.tools.metadata import tool_metadata


@tool_metadata(category=ToolCategory.KNOWLEDGE, risk=ToolRisk.MODERATE)
def create_or_update_skill(name: str, description: str, instructions: str) -> str:
    """Creates a new skill or updates an existing skill in data/skills/<name>/SKILL.md.

    Args:
        name: Unique identifier for the skill (e.g. 'frontend_styling', 'sql_optimizer').
        description: 1-2 sentence description explaining when to load/use this skill.
        instructions: Markdown text content containing the complete skill instructions.
    """
    skill = skill_registry.save_skill(
        name=name,
        description=description,
        instructions=instructions,
    )
    return (
        f"Skill '{skill.name}' was successfully saved and registered to"
        " data/skills/{skill.name}/SKILL.md!"
    )


@tool_metadata(category=ToolCategory.KNOWLEDGE, risk=ToolRisk.LOW, read_only=True)
def list_available_skills() -> str:
    """Lists all skills currently registered in the Kayak skills directory."""
    skills = skill_registry.list_skills()
    if not skills:
        return "No skills currently registered."

    output = ["=== Registered Skills ==="]
    for s in skills:
        output.append(f"- **{s.name}**: {s.description}")
    return "\n".join(output)
