---
"agent-harness-lab": patch
---

Pin what an interrupted behavioral evaluation leaves behind (part of #77). Tests interrupt a real cooperative `ahl improve` run while its paired trials run and after they finish but before the decision, and show that the target keeps its exact bytes, `workflow.json` reports `interrupted` with no automatic promotion, the trials that ended stay in an unfinished `results.json` (no `inputs_unchanged` or `elapsed_seconds`), no `change-plan.json` exists so `ahl apply` rejects the run with exit 2, `--resume` reports `resume_requires_intervention` without dispatching anything, and a plain rerun is a new run that leaves the interrupted one unchanged. No product code changes; the behavioral gate is unchanged.
