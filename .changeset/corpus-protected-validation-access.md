---
"agent-harness-lab": minor
---

Add one-shot protected validation access to the private diagnostic corpus: `CorpusStore.protected_inputs()` lists label-free validation texts bound to the exact corpus revision, and `evaluate_protected()` scores frozen per-case class scores against the validation labels with the v5 multi-class triage policy, releasing no label. Each labeled case is then retired to development with an exposure event naming the access ID, so a validation label supports one evaluation, and an invalid policy, score or model digest retires nothing. Calibration, threshold and certification partitions stay unreachable, reviewer identities remain operator-attested and no gold label, acceptance or classifier-selection authority is issued. Part of #73.
