from backend.app.skills.registry import skill_registry
from backend.app.models import ToolCategory, ToolRisk
from backend.app.tools.metadata import tool_metadata


@tool_metadata(category=ToolCategory.KNOWLEDGE, risk=ToolRisk.LOW, read_only=True)
def load_skill(skill_name: str) -> str:
    """Loads and retrieves the complete markdown instructions and knowledge for a specific skill.

    Args:
        skill_name: The name/identifier of the skill to load (e.g. 'coding_best_practices', 'web_researcher').
    """
    skill = skill_registry.get_skill(skill_name)
    if not skill:
        available = [s.name for s in skill_registry.list_skills()]
        return (
            f"Error: Skill '{skill_name}' not found. Available skills:"
            f" {', '.join(available)}"
        )

    output = [
        f"=== Skill Loaded: {skill.name} ===",
        f"Description: {skill.description}",
    ]
    if skill.helper_files:
        output.append(f"Helper files: {', '.join(skill.helper_files)}")

    output.append("\n--- Instructions ---")
    output.append(skill.instructions)

    return "\n".join(output)
