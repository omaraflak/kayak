from pathlib import Path
import re
import shutil
from typing import Dict, List, Optional, Tuple
import yaml
from backend.app.config import settings
from backend.app.models import Skill


def _clean_skill_name(name: str) -> str:
    """Sanitizes a skill name into a clean directory identifier."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name.lower().strip())


def _extract_frontmatter_and_instructions(
    content: str, default_name: str
) -> Tuple[str, str, str]:
    """Parses frontmatter metadata and returns (skill_name, description, instructions)."""
    skill_name = default_name
    description = ""
    instructions = content

    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            try:
                frontmatter = yaml.safe_load(parts[1])
                if isinstance(frontmatter, dict):
                    skill_name = frontmatter.get("name", skill_name)
                    description = frontmatter.get("description", "")
                instructions = parts[2].strip()
            except Exception:
                pass

    if not description:
        for line in instructions.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                description = line[:200]
                break

    return skill_name, description, instructions


def _parse_skill_folder(folder: Path) -> Optional[Skill]:
    """Reads a skill directory, extracting frontmatter, markdown instructions, and helper files."""
    if not folder.is_dir():
        return None

    skill_file = folder / "SKILL.md"
    if not skill_file.exists():
        skill_file = folder / "skill.md"

    if not skill_file.exists():
        return None

    try:
        content = skill_file.read_text(encoding="utf-8")
        name, description, instructions = _extract_frontmatter_and_instructions(
            content=content, default_name=folder.name
        )

        helpers = [
            f.name
            for f in folder.iterdir()
            if f.name.lower() not in ["skill.md", ".ds_store"]
        ]

        return Skill(
            name=name,
            description=description or f"Skill: {name}",
            instructions=instructions,
            helper_files=helpers,
        )
    except Exception as e:
        print(f"Error loading skill '{folder.name}': {e}")
        return None


class SkillRegistry:
    """Manages markdown skill packages loaded from disk."""

    def __init__(self):
        self._skills: Dict[str, Skill] = {}

    def load_all_skills(self):
        """Scans data/skills/<name>/SKILL.md and loads them."""
        self._skills.clear()
        skills_dir = settings.SKILLS_DIR
        if not skills_dir.exists():
            return

        for folder in skills_dir.iterdir():
            skill = _parse_skill_folder(folder)
            if skill:
                self._skills[skill.name] = skill

    def list_skills(self) -> List[Skill]:
        self.load_all_skills()
        return list(self._skills.values())

    def get_skill(self, name: str) -> Optional[Skill]:
        self.load_all_skills()
        return self._skills.get(name)

    def save_skill(
        self, name: str, description: str, instructions: str
    ) -> Skill:
        clean_name = _clean_skill_name(name)
        folder = settings.SKILLS_DIR / clean_name
        folder.mkdir(parents=True, exist_ok=True)

        content = (
            f"---\nname: {clean_name}\ndescription: {description}\n---\n\n"
            f"{instructions}\n"
        )
        (folder / "SKILL.md").write_text(content, encoding="utf-8")

        skill = Skill(
            name=clean_name,
            description=description,
            instructions=instructions,
            helper_files=[],
        )
        self._skills[clean_name] = skill
        return skill

    def delete_skill(self, name: str) -> bool:
        clean_name = _clean_skill_name(name)
        folder = settings.SKILLS_DIR / clean_name
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
            self._skills.pop(clean_name, None)
            return True
        return False


skill_registry = SkillRegistry()
