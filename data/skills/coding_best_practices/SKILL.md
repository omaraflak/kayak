---
name: coding_best_practices
description: Core guidelines for clean, defensive, and modular software engineering in Kayak.
---

# Coding Best Practices Skill

Follow these principles whenever writing or refactoring code:

1. **Inspect Before Editing**:
   - Always run `read_file` or `list_directory` before making assumptions about existing code or file structure.
   - Do not overwrite entire files when small targeted replacements with `edit_file` are possible.

2. **Defensive Programming**:
   - Handle exceptions gracefully with clear error messages.
   - Validate inputs and parameter types.

3. **Modularity & Simplicity**:
   - Write small, single-purpose functions.
   - Avoid deep nesting and unnecessary abstractions.
   - Favor explicit code over magic.

4. **Verify Work**:
   - After creating or editing code, run automated tests or test commands with `run_command` to verify that everything works before concluding your turn.
