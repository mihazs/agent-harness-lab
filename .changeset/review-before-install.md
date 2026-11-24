---
"agent-harness-lab": minor
---

An installing `ahl improve` run now has a fresh, independent session review a confirmed candidate against the harness files it changes before installing it. A candidate the reviewer calls `contradicts` or `test_specific`, or one with no usable review, is not installed: the run ends `no_change` with `candidate_inconsistent`, and `install_review` in the receipt carries the verdict, reason and conflicts.
