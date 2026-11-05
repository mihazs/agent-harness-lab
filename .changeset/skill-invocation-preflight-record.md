---
"agent-harness-lab": patch
---

Record the private skill invocation context (`invocation.json`, origin `skill`, `execution_authority=False`) as soon as an `ahl skills invoke` run directory exists, including runs that the configuration preflight blocks before evaluation and `--repair-contracts` runs. The receipt parity contract now requires `invocation.json` in the skill run and its absence from the CLI run. Receipts, statuses, exit codes and the `ahl improve` route are unchanged, and the context grants no authority.
