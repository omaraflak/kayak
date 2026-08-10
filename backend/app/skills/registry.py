import os
from pathlib import Path
import re
from typing import Dict, List, Optional
import yaml
from backend.app.config import settings
from backend.app.models import Skill


class SkillRegistry:

    def __init__(self):
        self._skills: Dict[str, Skill] = {}

    def load_all_skills(self):
        """Scans data/skills/<name>/SKILL.md and loads them."""
        self._skills.clear()
        skills_dir = settings.SKILLS_DIR
        if not skills_dir.exists():
            return

        for folder in skills_dir.iterdir():
            if not folder.is_dir():
                continue

            skill_name = folder.name
            skill_file = folder / "SKILL.md"
            if not skill_file.exists():
                skill_file = folder / "skill.md"

            if not skill_file.exists():
                continue

            try:
                content = skill_file.read_text(encoding="utf-8")
                description = ""
                instructions = content

                # Check for YAML frontmatter
                if content.startswith("---"):
                    parts = content.split("---", 2)
                    if len(parts) >= 3:
                        frontmatter = yaml.safe_load(parts[1])
                        if isinstance(frontmatter, dict):
                            skill_name = frontmatter.get("name", skill_name)
                            description = frontmatter.get("description", "")
                        instructions = parts[2].strip()

                # If no description in frontmatter, extract first non-header line
                if not description:
                    for line in instructions.splitlines():
                        line = line.strip()
                        if line and not line.startswith("#"):
                            description = line[:200]
                            break

                # List helper files in the skill folder
                helpers = [
                    f.name
                    for f in folder.iterdir()
                    if f.name.lower() not in ["skill.md", ".ds_store"]
                ]

                self._skills[skill_name] = Skill(
                    name=skill_name,
                    description=description or f"Skill: {skill_name}",
                    instructions=instructions,
                    helper_files=helpers,
                )
            except Exception as e:
                print(f"Error loading skill '{skill_name}': {e}")

    def list_skills(self) -> List[Skill]:
        self.load_all_skills()
        return list(self._skills.values())

    def get_skill(self, name: str) -> Optional[Skill]:
        self.load_all_skills()
        return self._skills.get(name)

    def save_skill(
        self, name: str, description: str, instructions: str
    ) -> Skill:
        clean_name = re.sub(r"[^a-zA-Z0-9_-]", "_", name.lower().strip())
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
        clean_name = re.sub(r"[^a-zA-Z0-9_-]", "_", name.lower().strip())
        folder = settings.SKILLS_DIR / clean_name
        if folder.exists():
            import shutil

            shutil.rmtree(folder)
            self._skills.pop(clean_name, None)
            return True
        return False


skill_registry = SkillRegistry()
