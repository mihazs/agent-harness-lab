---
"agent-harness-lab": patch
---

Add the pinned ruff linter (`ruff==0.17.2`, in a new `lint` extra that `task setup:test` installs). `pyproject.toml` selects pyflakes and syntax-error rules (`F`, `E9`) for Python 3.11 and excludes frozen fixtures. `task lint` and `task lint:fix` run it, CI lints before the suite, and the lefthook pre-commit hook lints staged Python files. The tree is clean: unused imports and variables are removed and deliberate re-exports are marked.
