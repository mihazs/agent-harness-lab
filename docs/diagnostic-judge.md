# Recorded-response controls for a pairwise diagnostic judge

This reference covers `agent_harness_lab.diagnostics.judge.evaluate(policy, observations)`, a
library API for issue #14. It checks responses that someone recorded earlier from a pairwise
diagnostic judge (a model or reviewer asked which of two arms, or neither, shows the failure)
against reviewed held-out labels. It never calls a model, needs no credentials and downloads
nothing, so it can test the protocol and its error handling without authorizing any inference.
Like the [cascade evaluation](diagnostic-cascade.md) and the [triage policy](diagnostic-triage.md),
it validates frozen, human-supplied limits and grants no authority.

A judge that agrees with itself is not evidence. A verdict counts only when it survives both
presentation orders, and it is compared with the supplied labels, not with itself.

## Input

Each row is a dict with `root_case_id`, `split: "heldout"`, `label` (`first`, `second`, `tie`, or
`null` for unknown) and `responses`: one recorded response per presentation order, `first_second`
and `second_first`. A response has `judge_ref`, `rubric_revision`, `presented`, `verdict` (`A`,
`B` or `tie`; `null` is an abstention), `citations` (evidence span identifiers; `[]` when the
response cited nothing) and an optional `confidence` that is validated and then ignored. The
`verdict` and `citations` keys are required: a response that omits one is refused, because
omission is not abstention and not an uncited claim. A response from another judge or rubric
revision than the policy's is refused, never mixed into one report.

Optional row fields:

- `spans`: the case's own evidence span identifiers, and `supporting_spans`: the subset a reviewer
  marked as supporting the label.
- `violating_arm`: an arm that an executable check flagged (`first` or `second`).
- `control`: `style`, `verbosity` or `wording` marks a matched control whose two arms differ only
  in that factor. Its label must be `tie`. Controls are counted apart from the case roots.

A root appears in one row; a repeated root, a non-held-out row, a label outside the vocabulary,
oversized identifier lists or an invalid measurement is refused. A verdict outside the vocabulary
is not refused: it is counted as `invalid_verdict` (see Report).

## Policy

```json
{"schema_version":1,"profile":"diagnostic-judge-policy-v1",
 "judge_ref":"...","rubric_revision":"...",
 "min_coverage":C,"min_label_agreement":A,
 "max_order_disagreement_rate":D,"max_control_preference_rate":P,
 "max_citation_failure_rate":F}
```

The letters are placeholders: AHL supplies no default. The two minimums must be above 0 and every
limit is a rate in `[0, 1]`. `judge_ref` and `rubric_revision` freeze the judge protocol; the
report carries the SHA-256 of the policy.

## Report

`diagnostic-judge-evaluation-v1` gives, per declared case root, one state:

- `decided`: both orders chose the same arm (or both said `tie`). Only this state is scored.
- `order_disagreement`: the orders conflict, which is what a verdict that follows the position
  rather than the arm looks like. `position_picks` counts the raw `A` and `B` answers.
- `abstained`: a recorded order abstained. This is not a disagreement.
- `order_unchecked`: only one order was recorded. It is never decided.
- `invalid_verdict`: a verdict outside `A`, `B`, `tie`, `null`.

`coverage` is the decided share of the case roots, so an always-abstaining judge reports zero.
`label_agreement` counts decided roots whose arm matches the label as correct or wrong; unknown
labels are counted in `unknown_labels` and are never wrong answers. Matched controls report the
share that resolved to an arm instead of a tie, in total and `by_factor`; a control that is not
decided is listed as unresolved, not as a pass.

Citations on a decided, non-tie root are checked against the case's own spans:

- `fabricated`: a cited identifier is not one of the case's spans.
- `unsupported`: every identifier exists, but the verdict or the cited spans do not match the
  reviewed support.
- `supported`: the verdict equals the label and the citations are within the reviewed support.
- `existence_only`: the identifiers exist but no reviewed support or label is available.
- `unverifiable`: the row declares no spans.
- `uncited`: the response cited nothing.

Existence of a citation is not support for the claim. The failure rate counts `fabricated`,
`unsupported` and `uncited` over the checkable citations; `existence_only` and `unverifiable`
stay visible and make the report list `citations_unverified`.

A decided root where the judge prefers the arm an executable check flagged, or calls the arms
equal, counts under `executable_violations_overridden`. The judge cannot clear an executable
violation, and abstentions and order disagreements are not overrides.

The status is `rejected` when coverage or label agreement is below its minimum, when order
disagreement, control preference or citation failure is above its maximum, or when an executable
violation was overridden. Otherwise it is `insufficient_evidence`. A rate with no denominator is
`null`, not zero, and its check is skipped, with `zero_coverage`, `no_labeled_decisions`,
`orders_unchecked`, `invalid_verdicts_present`, `no_controls`, `controls_unresolved` or
`citations_unverified` listed in `reasons`. The report always lists `blinding_unverified`,
`label_authority_unverified`, `root_independence_unverified` and
`policy_preregistration_unverified`: AHL does not verify that the judge was blind to the treatment
identity, that the labels are authoritative, that the roots are independent or that the policy
was frozen before the held-out data were seen.

Self-reported confidence is never read; the report says `self_reported_confidence:
ignored_uncalibrated`. The report never issues a diagnosis or a beneficial verdict:
`authoritative_diagnosis: false`, `beneficial_claim: not_issued`, `certification: not_issued`,
`automatic_promotion: false`, `promotion_authority: false` and `publication_allowed: false`.
Reports carry counts only, not root identifiers or span identifiers. A synthetic pass is a
protocol check, not a real-world accuracy claim.
