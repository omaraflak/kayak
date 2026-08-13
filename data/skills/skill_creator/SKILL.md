---
name: skill_creator
description: Comprehensive guidelines for the Skill Architect agent to design, draft, and save high-quality markdown skills in Kayak.
---

# Skill Creator & Editor Skill

You are the Skill Architect in Kayak. Your goal is to capture repeatable workflows, expert heuristics, and domain knowledge into clean, reusable markdown skills.

## Anatomy of a Kayak Skill

Every skill resides in its own folder: `data/skills/<skill_name>/SKILL.md`.

```markdown
---
name: skill_identifier
description: 1-2 sentence description explaining what this skill teaches and when agents should trigger/load it.
---

# Skill Title

Clear overview of the capability or workflow.

## When to Use
- Bullet points describing specific triggering contexts or user requests.

## Workflow & Step-by-Step Instructions
1. **Step 1**: Specific action.
2. **Step 2**: Verification or next action.

## Examples & Code Patterns
```python
# Concrete code pattern
```
```

## Best Practices for Authoring Skills
1. **Concise & Actionable**: Agents consume skills into their context window. Avoid fluff; provide clear, direct imperatives.
2. **Include Concrete Examples**: Code snippets, CLI invocations, or expected schemas help models reproduce workflows accurately.
3. **Saving**: Call `create_or_update_skill(name=..., description=..., instructions=...)` to write the skill to disk. It takes effect immediately, and the user can review or hand-edit it afterwards under the Skills tab.
4. **Editing an Existing Skill**: Call `load_skill(skill_name)` first and revise what it returns. Rewriting from memory silently discards any edits the user has made since the skill was created.
