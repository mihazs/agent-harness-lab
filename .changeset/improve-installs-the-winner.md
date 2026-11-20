---
"agent-harness-lab": minor
---

`ahl improve` now installs the evaluated winning candidate into the project through the rollback journal and ends `applied`, instead of always ending `inconclusive` with a recommendation. A candidate that fixes a task and loses none counts as a winner even when other tasks still fail. A project `ahl.json`, a user-level scope and `ahl watch` keep the recommendation.
