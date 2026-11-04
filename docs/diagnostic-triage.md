# Frozen incident-triage policies

`ahl benchmark` can evaluate an explicit binary policy on the input's `heldout`
partition, separately from its descriptive calibration-threshold smoke report:

```sh
ahl benchmark examples.jsonl --method baseline --triage-policy policy.json
```

The policy supplies every constraint; AHL does not invent precision, recall or
coverage targets:

```json
{"schema_version":1,"threshold":0.9,"min_precision":0.8,"min_recall":0.7,"min_coverage":0.5}
```

`threshold` is the minimum confidence `max(p, 1-p)` needed for a decision. Scores
at least 0.5 predict an incident. Multiple observations of one declared root
count once; conflicting decisions within that root abstain. Incident recall
includes positive roots that abstain in its denominator. Unknown labels do not
become negatives, and undefined metrics remain null.

The `triage` object contains the frozen policy hash, threshold, root decisions,
TP/FP/FN/TN, class-specific abstentions and failed constraints. These decisions
use `triage.threshold`; the legacy top-level `predictions` use the separate
descriptive `threshold` and have `threshold_authority: descriptive_smoke_only`.

An observed constraint violation returns `rejected`. Otherwise the result is
`insufficient_evidence`: supplied root IDs and labels do not establish root
independence, independent validation, or oracle authority. No policy from this
interface is accepted for deployment or harness promotion. Freeze the policy
before inspecting validation data; this input format cannot prove that history.

This first slice of issue #74 does not fit a calibrator, tune a policy, issue
uncertainty guarantees, or replace the runtime's existing uncalibrated abstention.
