---
"agent-harness-lab": minor
---

`ahl experiment --spec PLAN --power-model MODEL` now also accepts a frozen plan that declares the sequential profile. It draws synthetic potential outcomes under the declared model and judges each draw twice with the unchanged engines, `sequential.analyze` after every observed root block under the controller's own stopping rule and `statistics.analyze` over every declared task as the fixed comparator, then reports both profiles' disposition rates with Wilson 95% intervals, the roots and trials inspected before stopping with normal-approximation 95% intervals, the mean fraction of the fixed trials used, and false-support and false-harm-call rates against the drawn bank's realized root-block means (`sequential-root-power-simulation-v1`). Fixed plans keep the existing simulator; the report is private, runs no worker, model or grader, changes no plan or budget and cannot authorize promotion.
