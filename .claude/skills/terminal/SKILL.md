---
name: terminal
description: Use when executing shell / terminal commands. Use shell often!
---

- Ensure you are in the project root directory.
- First check the scripts/ folder to see whether there is already a utility that can be used for the task.
- Prefer writing and executing scripts over running commands directly (except ls, pwd, cat, cd, and similar).
- Place new scripts in scripts/ with descriptive names.
- Preferred scripting language is Python via `uv run python ...`; shell is fine for simple things.
- Always use `uv` for running Python, adding dependencies, or updating project settings — never call `python`/`pip` directly.
- Do not over-engineer scripts with edge-case handling; assume the happy path, but make liberal use of parameters instead of hardcoded values.
