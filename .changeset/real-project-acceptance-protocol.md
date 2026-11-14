---
"agent-harness-lab": minor
---

Document the real-project acceptance protocol and run its synthetic equivalent in CI (FU-6, part of #802). The new how-to `docs/real-project-acceptance.md` runs the first-use, before/after, no-fake-results, incremental and stress steps through `scripts/isolated_project_check.py`, states the PASS, FAIL and KNOWN_GAP criteria per step, keeps every private output under one directory (including the AHL run directories, through `--pass-env AHL_STATE_HOME`) and says how to delete it, including the runtime's own session records. `tests/integration/tools/test_acceptance_protocol.py` runs the same steps on a synthetic repository, recorded sessions and a stub runtime, and `task test:isolated` without `REPO` now runs it as the opt-in rehearsal. No product behavior changes. Closes #808.
