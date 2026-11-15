---
"agent-harness-lab": minor
---

Add `agent_harness_lab.diagnostics.localization`: `localize` turns the violated results of a case's constraint report into a private `constraint-localization-v1` report (part of #10). Each violated constraint lists its symptom events, the earliest visible ones, and a bounded candidate set with an enumerated basis and a relation that is `upstream` or `downstream` only where the capture declared the order, plus the competing causes, the controlled replay that could falsify it and the recovered-error and changing-state negative controls. Upstream evidence that was not captured stays `not_established`, an `unknown` result stays `unresolved`, and a broken grader is an `evaluator_or_label_defect` candidate rather than an instruction defect. `mechanism_records` drafts unverified `MechanismRecord` rows from an untampered report; they keep the `insufficient_evidence` component and need a reviewer's route.
