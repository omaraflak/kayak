from typing import Dict, List
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from backend.app.models import Skill
from backend.app.skills.registry import skill_registry

router = APIRouter(prefix="/api/skills", tags=["skills"])


class SaveSkillRequest(BaseModel):
    """Payload for creating or editing a markdown skill package."""
    name: str
    description: str
    instructions: str


@router.get("", response_model=List[Skill])
async def list_all_skills() -> List[Skill]:
    """Lists all available markdown skills loaded from data/skills.

    Returns:
        A list of Skill model representations.
    """
    return skill_registry.list_skills()


@router.post("", response_model=Skill)
async def save_skill(request: SaveSkillRequest) -> Skill:
    """Creates or updates a skill's SKILL.md file and frontmatter.

    Args:
        request: Payload containing skill name, description, and markdown instructions.

    Returns:
        The newly saved Skill model object.
    """
    return skill_registry.save_skill(
        name=request.name,
        description=request.description,
        instructions=request.instructions,
    )


@router.delete("/{skill_name}")
async def delete_skill(skill_name: str) -> Dict[str, str]:
    """Deletes a skill directory from data/skills.

    Args:
        skill_name: Unique skill identifier.

    Returns:
        Status response dictionary.

    Raises:
        HTTPException: If the skill could not be found or deleted.
    """
    success = skill_registry.delete_skill(skill_name)
    if not success:
        raise HTTPException(
            status_code=404, detail=f"Skill '{skill_name}' not found"
        )
    return {"status": "deleted"}
