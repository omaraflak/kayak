---
name: test_driven_development
description: Test-Driven Development (TDD) workflow for drafting unit tests before implementation.
---

# Test-Driven Development (TDD) Skill

When implementing new features or bugfixes:

1. **Write the Test First**:
   - Create a test file (e.g. `test_<feature>.py` or `verify.py`) defining expected inputs, outputs, and edge cases.
2. **Run the Test (Red)**:
   - Execute the test with `run_command("pytest test_feature.py")` to verify it fails for the right reason.
3. **Implement Minimum Code (Green)**:
   - Write the simplest clean code to pass the test.
4. **Refactor & Verify**:
   - Clean up code while keeping tests green.
