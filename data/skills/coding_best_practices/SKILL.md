---
name: coding_best_practices
description: Core guidelines for efficient, clean, and verifiable software engineering in Kayak.
---

# Coding Best Practices Skill

## Work efficiently — tool calls are a budget

Each turn allows a limited number of tool steps. Wasted calls mean unfinished work,
so structure every task like this:

1. **Locate before searching.** Files the user uploads are in the workspace root.
   Use `list_directory` or `find_files` (recursive and case-insensitive: `*.jpg`
   also matches `.JPG`). Never scan the filesystem with `find /`.
2. **Check the environment once.** Verify the imports you need in a single step,
   and install everything missing in one `pip install pkg1 pkg2 pkg3`. Do not use
   apt-get.
3. **Keep state between steps.** Use `run_python` for exploration and analysis: it
   is one persistent session, so data you load stays loaded. Do not chain
   `python3 -c` one-liners through `run_command` — each one is a fresh process
   that repeats all previous work.
4. **Write the deliverable early.** As soon as the approach is clear, put the real
   script in a file with `write_file`, run it, and refine it with `edit_file`.
   Do not leave writing the final artifact to the end of the turn.
5. **Fail twice, rethink.** If the same approach errors twice, change strategy
   instead of retrying variants of the same command.

## Code quality

- **Inspect before editing**: `read_file` before `edit_file`; prefer small targeted
  replacements over rewriting whole files.
- **Modularity & simplicity**: small single-purpose functions, no deep nesting,
  explicit code over magic.
- **Defensive programming**: validate inputs and raise clear error messages.

## Verify work

- After creating or editing code, run it (or its tests) with `run_command`.
- Confirm the promised outputs actually exist — list the generated files — before
  concluding your turn.
