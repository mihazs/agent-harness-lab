---
"agent-harness-lab": patch
---

Close two gaps that let an evaluation that proves nothing look like a result. The `before_after` block of an `ahl improve` receipt kept reporting `improved` with scores and a delta for a run to which a vacuity label applied (for example trials without a recorded grader execution); it is now `inconclusive` with `evaluation_vacuous` and the labels among its reasons, carries a `vacuity` block, reports `null` scores and delta, and never blocks adoption on a regression read from such a run. The improvement loop kept its own copy of the five vacuity labels; it now reads the one closed set in `agent_harness_lab.evaluation.vacuity`, so a label added or removed there changes the loop as well, as the loop epic asked in the issue. The vacuity label reference lists both surfaces in its audit. Part of #805.
