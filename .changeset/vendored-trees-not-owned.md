---
"agent-harness-lab": patch
---

Vendored and generated trees are no longer adoptable harness surfaces. A path below `node_modules`, `.venv`, `venv`, `vendor`, `bower_components`, `jspm_packages`, `site-packages`, `__pypackages__`, `.tox` or `.nox` is at most `unsupported` and carries the `vendored` facet, and the harness inventory still lists it with the fact `vendored: true`. The promotion journal also refuses a nested path whose folder the project's ignore rules exclude, through a read-only `git check-ignore --no-index`; an unknown git answer fails closed. Their `CLAUDE.md`, `AGENTS.md`, `.claude/rules` and skill files could previously be journaled because those trees hold no `.git` of their own.
