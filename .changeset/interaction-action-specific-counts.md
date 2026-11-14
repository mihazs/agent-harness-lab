---
"agent-harness-lab": minor
---

Add `evaluation.interaction_metrics.summarize`, which grades a bounded list of `(contract, evidence)` interaction trials and reports unnecessary action, action before the reply, missed and unnecessary clarification, unjustified stop, abnormal termination, state preservation, correct deliberate stops and full completion as separate counts with unknowns kept apart from failures. It computes no rate or single score, grants no publication or promotion, and is not wired into `run_evaluation.py`. Part of #123.
