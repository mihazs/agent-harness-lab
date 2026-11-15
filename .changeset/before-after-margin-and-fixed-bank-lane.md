---
"agent-harness-lab": minor
---

Finish the before/after guard for the two lanes it did not reach. A sealed fixed repeated-bank comparison (`--experiment-request`) now carries the same `before_after` block, built from the controller's recorded graded trials with the request's own `family_margin` as the tolerated loss and `effect_margin` as the bar for `improved`; a candidate worse beyond the tolerated loss ends `blocked` with reason `candidate_regressed`, drops the `candidate_not_supported` recommendation and writes `investigation.json`, instead of ending `inconclusive`. `ahl improve --regression-margin FRACTION` declares the tolerated loss for the default lane (default `0`, any net loss); an out-of-range value is an input error and the option cannot be combined with `--experiment-request`.
