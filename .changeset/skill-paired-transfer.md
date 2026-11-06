---
"agent-harness-lab": minor
---

Add `evaluation.skills.paired_transfer`, a paired skill-present versus skill-absent report over matched task roots under a predeclared target/control policy with a declared `max_control_loss`. A skill that helps its target tasks but loses more than the tolerance on an unrelated control is rejected as negative transfer, and a critical regression also rejects it. An observed activation in the absent arm, or missing matched target or control roots, is inconclusive. Preload stays a diagnostic condition, and the main contrast never filters on activation. Activation counts only as an `activated: true` record with evidence. An injected hash, `activated: false` or a bare label stays unknown. Unverified and unidentified records are excluded and counted. The report never certifies or promotes.
