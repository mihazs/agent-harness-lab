# Cascade evaluation of a second diagnostic stage

This reference covers `agent_harness_lab.diagnostics.cascade.evaluate(policy, observations)`,
a library API for issue #14. It checks whether a second diagnostic stage (a verifier,
larger model or reviewer-style classifier) changed the first stage's answers for the better on
held-out declared roots. It does not run a model, fit anything or select a threshold. Like the
[triage policy](diagnostic-triage.md), it validates frozen, human-supplied limits.

The first prediction is preserved. A second stage that fixes some answers is reported next to
the answers it broke, so corrections alone never read as a benefit.

## Input

Each row is a dict with `root_case_id`, `split: "heldout"`, `label` (a declared class or `null`
for unknown) and `first` (a declared class, or `null` when the first stage abstained). A `second`
key means the second stage ran: its class replaces the first answer, and `null` (abstention)
leaves the first answer in place. A row without `second` never ran the second stage. The
optional `first_cost`, `second_cost`, `first_latency_seconds` and `second_latency_seconds` are
caller-measured nonnegative numbers. A root appears in one row; a repeated root, a non-held-out
row, a class outside the policy or an invalid measurement is refused.

## Policy

```json
{"schema_version":1,"profile":"diagnostic-cascade-policy-v1",
 "classes":["class_a","class_b"],"severe_classes":["class_a"],
 "min_net_errors_removed":N,"max_introduced_errors":K,"max_severe_misses":S,
 "max_review_roots":R,"max_total_cost":C}
```

The letters are placeholders: AHL supplies no default. `min_net_errors_removed` must be at least 1,
so a cascade is never accepted for removing nothing. `max_total_cost` may be `null` to declare no
cost limit; the totals are still reported.

## Report

`diagnostic-cascade-evaluation-v1` counts labeled roots as correct, wrong or abstained before
(`first`) and after (`final`) the second stage, and gives these transitions:

- `corrected_errors`: first wrong, final correct.
- `resolved_abstentions`: first abstained, final correct.
- `introduced_errors_from_correct` and `introduced_errors_from_abstention`: final wrong although
  the first stage was correct or had abstained. `introduced_errors` is their sum.
- `net_errors_removed`: first-stage wrong roots minus final wrong roots. It can be negative.

`severe_misses` counts labeled severe-class roots that are not answered correctly, before and
after. `review_roots` counts abstentions that remain for human review. `resources` totals cost
and latency over the stages that ran; a total is `null`, never zero, when any run lacks a
measurement, and the report lists `cost_unmeasured` or `latency_seconds_unmeasured`.

The status is `rejected` when the net error removal is below the policy minimum, introduced errors,
final severe misses or review roots exceed their maximum, final severe misses exceed the first
stage's, or measured cost exceeds its limit. Otherwise it is `insufficient_evidence`. The report
never issues a beneficial verdict: `beneficial_claim: not_issued`, `certification: not_issued`,
`automatic_promotion: false` and `publication_allowed: false`. It does not verify label authority,
root independence or that the policy was frozen before the held-out data were seen. Unknown labels
are counted separately and never become negatives; a run with no labeled roots lists
`no_labeled_roots`. Reports carry counts only, not root IDs.

Self-consistent agreement between the two stages is not evidence: only the supplied labels are
compared. A synthetic pass is a protocol check, not a real-world accuracy claim.
