# Backend prediction envelope

This reference covers `agent_harness_lab.diagnostics.backend_envelope.evaluate(frozen, records)`, a
library API for issue #14. Before diagnostic backends (a majority class, rules, TF-IDF, embeddings, NLI,
a local encoder or recorded responses) can be compared on the same held-out roots, each answer needs one
versioned record of what produced it. The envelope is that record, and the check reports which backends
answered which frozen roots. It runs no model, fits nothing, scores no label and ranks no backend, so it
is the schema step that comes before a frozen benchmark runner and a comparison report. Like the
[cascade evaluation](diagnostic-cascade.md) and the [judge controls](diagnostic-judge.md), it validates a
frozen, human-supplied declaration and grants no authority.

## Frozen declaration

```json
{"schema_version":1,"profile":"diagnostic-backend-frozen-v1",
 "taxonomy_revision":"...","classes":["class_a","class_b"],"roots":["root-1","root-2"]}
```

Declare the taxonomy revision, at least two unique classes and the eligible held-out roots before any
backend runs. A backend that answers a root outside the list, or states another taxonomy revision, is
refused rather than mixed in.

## Envelope

One envelope per backend and root, with exactly these keys (a missing key is unknown capture and is
refused; use an explicit `null` where the table allows it):

| Key | Content |
| --- | --- |
| `schema_version`, `profile`, `root_case_id`, `split` | `1`, `diagnostic-prediction-envelope-v1`, a frozen root, `heldout` |
| `backend` | `kind` (`majority`, `rules`, `tfidf`, `embeddings`, `nli`, `local_encoder`, `recorded_response`), `ref`, `revision`, `preprocessing_sha256` (64 hex characters), `taxonomy_revision`, `score_semantics` (`none`, `rank_score`, `similarity`, `entailment`, `self_reported_confidence`, `calibrated_probability`), `calibration_ref` (`null` when none) |
| `fit` | `roles`: the splits the backend was fitted, calibrated or tuned on (`train`, `calibration`, `development`, `heldout`); `demonstration_roots`: roots shown to the backend as examples |
| `input` | `fields`: the field names given to the model; `max_chars`: the evidence-window limit; `truncated`: `true`, `false` or `null` when unknown |
| `outcome` | `status` `predicted` (a declared `label`, `reason` `null`), `abstained` or `failed` (`label` `null`, a `reason`); `score` is a number or `null` and only a prediction carries one |
| `resources` | `phase` `cold` (loading or training) or `warm` (per-record), then `latency_seconds`, `peak_memory_bytes` and `cost`: caller-measured nonnegative numbers, `null` when unmeasured |

An abstention and a failed run are different outcomes and are never inferred from a missing record.
A label outside the declared classes, a prediction without a label, an abstention that names one, a
boolean or non-finite number and a repeated backend and root are refused.

## Report

`diagnostic-backend-envelope-report-v1` gives, per backend `ref`, the counts of `predicted`, `abstained`
and `failed` envelopes, `missing_roots` (frozen roots with no envelope), `coverage` (predictions over
all frozen roots, so a missing root is uncovered, never dropped) and the `truncated` split into `yes`,
`unknown` and `no`. `resources` totals latency and cost and takes the largest peak memory per phase;
a total is `null`, never zero, when any envelope in that phase lacks a measurement, and the phase lists
what was `unmeasured`. Cold and warm are never added together. `comparable_roots` counts the roots every
backend answered with any outcome.

The status is `rejected` when any envelope shows:

- `heldout_role_used_in_fit`: the backend was fitted, calibrated or tuned on the held-out split.
- `demonstration_root_is_evaluated`: a demonstration root is one of the frozen evaluated roots.
- `label_or_reward_in_model_input`: an input field is named like a label, reward, ground truth, outcome,
  grade, expected value or answer. Names are compared after trimming and lowercasing; a differently
  named field that carries a label is not detected.
- `unsupported_probability_claim`: a score declared `calibrated_probability` by an `nli` or
  `recorded_response` backend (entailment and stated confidence are never probabilities), or without a
  `calibration_ref`.
- `backend_identity_changed`: one `ref` appears with another revision, preprocessing hash, score meaning,
  calibration reference, fit, input fields or window. Its `identity_sha256` is then `null`, because a changed
  model, prompt or preprocessing invalidates the cache and calibration identity.

Otherwise it is `insufficient_evidence`. The reasons also list, when they apply, `incomplete_backend_results`,
`zero_coverage_backend` (an always-abstaining or always-failing backend exposes its zero coverage),
`truncation_unknown`, `evidence_windows_differ` (backends did not see the same input fields and window, so
the comparison is not same-input) and `calibration_unverified` (a declared `calibration_ref` is recorded,
never checked), plus, always, `label_authority_unverified`, `root_independence_unverified` and
`frozen_declaration_preregistration_unverified`.

The report never computes label quality (`quality: not_computed`), selects a backend (`selection: not_issued`)
or grants authority: `beneficial_claim: not_issued`, `promotion_authority: false` and
`publication_allowed: false`. It carries counts and backend references only, never roots or inputs.
A complete synthetic report is a protocol check, not a real-world accuracy claim.
