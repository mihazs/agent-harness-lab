---
"agent-harness-lab": patch
---

Fix the CLI/skill receipt parity check that failed at random: the measured `evaluation_construction_seconds` (and each construction repair's `elapsed_seconds`, also stored in `recovery.json` and `evaluation-construction-N.json`) now joins the documented measured durations that may differ between `ahl improve` and `ahl skills invoke`, and a guard test derives every clock-derived receipt field from the code so a new timing field cannot reintroduce the flake. Parity still fails for every other difference. The private evaluation toolkit copy now skips `__pycache__` bytecode, which a concurrent process could add to the source tree between two runs and make their run directories differ. No receipt, status or exit code changes.
