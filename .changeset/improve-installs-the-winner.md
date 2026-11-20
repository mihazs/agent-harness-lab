---
"agent-harness-lab": minor
---

`ahl improve` now installs the evaluated winning candidate into the project through the rollback journal and ends `applied`, instead of always ending `inconclusive` with a recommendation. A candidate that fixes a task and loses none counts as a winner even when other tasks still fail, and a tie is installed too, labelled `no_regression_observed`: only a baseline that scored better ends in no change. A project `ahl.json`, a user-level scope and `ahl watch` keep the recommendation.
