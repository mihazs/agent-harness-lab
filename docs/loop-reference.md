# Loop declaration and state machine reference

This page is the reference for the acceptance score declaration and the
evaluate > improve > evaluate state machine in `agent_harness_lab.loop`. Both are
pure: no model call, clock, network or file access beyond reading configuration.
They define what a later loop controller may do, and nothing runs them yet:
`ahl improve` still makes one pass, and no command, receipt or exit code changes.

The loop only recommends. A declaration sets a bar and a stop policy; it never
grants apply, global scope, promotion or certification, and every resolved spec
carries `recommendation_only: true`, `automatic_promotion: false` and
`certification: unverified`.

## Declaration fields

A loop declaration is one JSON object with exactly these optional fields. Any
other field is rejected with `loop_field_unknown`.

| Field | Meaning | Default | Accepted values | Rejection reason |
| --- | --- | --- | --- | --- |
| `accept_score` | The bar: the success rate on the frozen bank's scoring partitions, as a number or a map of declared area ids to numbers | none, see [relative mode](#relative-mode) | a number above 0 and at most 1, or a non-empty map of declared area ids to such numbers; never a boolean, string, null, NaN or infinity | `loop_accept_score_invalid`, `areas_not_declared`, `loop_area_undeclared` |
| `accept_basis` | What must reach the bar | `lower_bound` (the interval lower bound) | `lower_bound` only; a point-value basis is absent because every score is an interval | `loop_accept_basis_invalid` |
| `regression_margin` | How much worse than the best counts as a regression | `0.0` | a number from 0 up to but not including 1 | `loop_margin_invalid` |
| `noise_margin` | The existing `effect_margin` meaning: a gain inside it is noise | `0.0` | a number from 0 up to but not including 1 | `loop_margin_invalid` |
| `max_rounds` | The most improvement rounds; the hard cap is 10 | `3` | a whole number from 1 to 10 | `loop_max_rounds_invalid` |
| `plateau_rounds` | Consecutive rounds without an improvement that end the loop | `2`, or `max_rounds` when that is smaller | a whole number from 1 up to `max_rounds` | `loop_plateau_rounds_invalid` |
| `on_regression` | What a regression does | `stop` | `stop` or `rollback_continue` | `loop_on_regression_invalid` |
| `alpha` | The total error level over all rounds | `0.05` | a number above 0 and below 1 | `loop_alpha_invalid` |

The time and trial ceilings are not redeclared here. The resolver takes the
project's `limits` (`seconds`, `trials`) by reference and records them in the
spec; a `limits` key inside a loop declaration is `loop_field_unknown`. A limit
that is not a positive number of seconds and a positive whole number of trials is
`loop_limits_invalid`.

### Relative mode

With no `accept_score` the loop runs in the documented default mode
`relative_supported_improvement`: it is meant to stop when the existing analysis
reports a supported improvement over the round-0 baseline. The spec labels it
`acceptance_source: default` and never invents an absolute score. A declared
number gives mode `absolute`, and a map gives mode `per_area`.

A per-area map needs areas declared in the project `areas` list (see
[declare capability areas](declare-capability-areas.md)). Without any,
the reason is `areas_not_declared`; a key that is not a declared area is
`loop_area_undeclared`.

## Sources and precedence

A value comes from the first of these that declares it:

1. a command-line flag (`cli`);
2. the user file (`user`), the optional `loop` object in the user-level
   `user-config.json` beside the remembered runtime, which keeps its required
   `runtime` key and its owner-only mode (`ahl config clear` deletes the whole file);
3. the project `ahl.json` `loop` object (`project`);
4. the documented default (`default`).

Each field resolves on its own, and the resolved spec names the source of every
field and of `limits`. A source that is present but invalid is a typed error, not
a fallback to the default: an unreadable or unsafe user file is ignored with its
reason, but a user `loop` object that fails the rules above stops the resolver.
The `loop` object is an optional field of `ahl.json`, checked when the
configuration is loaded; without it `ahl.json` is unchanged.

The round bounds are checked across sources: `plateau_rounds` above the
resolved `max_rounds` is `loop_plateau_rounds_invalid`, blamed on the source that
declared the plateau.

### Authority is never declared

These keys are refused in every source, even with a harmless value:
`recommendation_only`, `automatic_promotion`, `apply`, `global`, `scope`,
`promotion`, `publication_allowed` and `certification`. The reason is
`loop_authority_refused`. A project configuration may declare a bar but never
an authority.

## The resolved spec

`agent_harness_lab.loop.spec.resolve` returns an immutable `LoopSpec`; `to_dict`
reports `profile` (`ahl-loop-spec-v1`), `schema_version`, the eight declared
fields, `limits`, `mode`, `acceptance_source`, `looks` (equal to `max_rounds`),
`per_look_alpha`, `sources`, the three authority constants and `digest`.

Each round is one look at the same frozen bank, so the error level of one look is
`per_look_alpha = alpha / max_rounds` (Bonferroni). It is part of the identity.

`digest` is the SHA-256 of the canonical JSON (sorted keys, compact separators)
of the declared values, `limits`, `mode`, `per_look_alpha` and the authority
constants. Changing any of them changes it. Where a value came from is a label
and not part of the identity, so the same values from a flag or from `ahl.json`
have one digest. A spec built directly is checked by `validate`, `digest` and
`to_dict` with the same rules.

## States and events

| State | Meaning |
| --- | --- |
| `declared` | The spec is frozen; nothing is evaluated. |
| `baseline_evaluated` | The baseline has an evaluation, valid or not. |
| `improving` | A round started and its candidate is not evaluated yet. |
| `candidate_evaluated` | The candidate has an evaluation on the same frozen inputs. |
| `decided` | The round's result is recorded. |
| `stopped` | One stop criterion is recorded; no event follows. |

`step(run, event, value)` applies one event and returns a new `Run`; `replay`
applies a sequence from `declared`.

| Event (value) | From | To |
| --- | --- | --- |
| `baseline_evaluated` (`valid`; anything else is read as not valid) | `declared` | `baseline_evaluated` |
| `round_started` | `baseline_evaluated`, `decided` | `improving` |
| `candidate_evaluated` (`valid`; anything else is read as not valid) | `improving` | `candidate_evaluated` |
| `decided` (an outcome) | `candidate_evaluated` | `decided` |
| `stopped` (a stop criterion) | see the stop table | `stopped` |

`round_started` also needs a valid evaluation of the current best, fewer than
`max_rounds` rounds started, and no preceding `accepted` result; a preceding
`regressed` result needs `on_regression: rollback_continue`. `decided` with
`accepted` or `improved` needs a valid candidate evaluation. Every move the table
does not allow raises `loop_order_violation`, naming `state.event`.

## Outcomes and stop criteria

The closed outcomes are `accepted`, `regressed`, `improved` and `inconclusive`.
A result that is `inconclusive` never counts as an improvement.

| Stop criterion | May be recorded from | Also requires |
| --- | --- | --- |
| `accepted_without_change` | `baseline_evaluated` | a valid baseline and no round started |
| `acceptance_score_reached` | `decided` | the last outcome is `accepted` |
| `max_rounds` | `decided` | `max_rounds` rounds started |
| `budget_exhausted` | any state before `stopped` | none |
| `plateau` | `decided` | the plateau streak reached `plateau_rounds` |
| `regression` | `decided` | the last outcome is `regressed` |
| `no_actionable_mechanism` | `baseline_evaluated`, `decided` | none |
| `evidence_insufficient` | `declared`, `baseline_evaluated`, `candidate_evaluated`, `decided` | none |
| `comparison_invalid` | `baseline_evaluated`, `improving`, `candidate_evaluated` | none |
| `interrupted` | any state before `stopped` | none |

## Decision table

`decide(inputs, spec)` returns `(outcome, stop criterion or None)`. A stop of
`None` means the loop goes on to another round; with no candidate yet, the
outcome `inconclusive` then means no verdict so far. `Inputs` holds classified
evidence; `None`, or any value outside a field's closed set, is unknown.

| Input | Values |
| --- | --- |
| `baseline` | `valid`, `vacuous`, `incomplete` |
| `candidate` | `absent` (the baseline-only decision), `valid`, `vacuous`, `incomplete`, `comparison_invalid` |
| `acceptance` | `met`, `not_met`, for the candidate, or the baseline when the candidate is absent |
| `regression` | `none`, `margin`, `critical`, `area`, against the best |
| `regression_cause` | `candidate`, `other` |
| `improvement` | `supported`, `noise`, over the best |
| `mechanism` | `available`, `none`, for another round |
| `budget` | `covers_round`, `exhausted`, covering the improvement and its re-evaluation |
| `round` | rounds started including this one, 0 before the first |
| `plateau_streak` | rounds without an improvement before this one |

The rules apply in this order, and the first that holds decides:

1. A candidate that is `comparison_invalid` gives `inconclusive` and `comparison_invalid`.
2. A baseline that is not `valid`, or a candidate that is not `absent` or `valid`, gives `inconclusive` and `evidence_insufficient`. A vacuous, incomplete or unknown evaluation never gives `accepted` or `improved`.
3. With no candidate, `acceptance` `met` gives `accepted` and `accepted_without_change`; an unknown `acceptance` gives `evidence_insufficient`; otherwise the limits below apply.
4. An unknown `regression` gives `evidence_insufficient`. A regression gives `regressed` and `regression`, unless `on_regression` is `rollback_continue`, the cause is `candidate` and a mechanism is `available`: then the limits apply and the outcome stays `regressed`.
5. `acceptance` `met` with no regression gives `accepted` and `acceptance_score_reached`; an unknown `acceptance` gives `evidence_insufficient`.
6. `improvement` `supported` gives `improved`, and `noise` gives `inconclusive`; an unknown `improvement` gives `evidence_insufficient`. Both then apply the limits.

The limits give the first of: `evidence_insufficient` when `round`, the
plateau streak, `budget` or `mechanism` is unknown; `max_rounds` when `round`
has reached `max_rounds`; `budget_exhausted`; `plateau` when the streak after
this round reached `plateau_rounds` (an improvement resets it to 0);
`no_actionable_mechanism`. When none holds the stop is `None`.

## Order invariants

1. Improve requires a prior valid evaluation of the current best.
2. A result requires a post-improvement evaluation, except `accepted_without_change`, which has no improvement.

The state machine enforces both and raises `loop_order_violation` otherwise.

## Typed reasons

Each reason has one remediation in the [exit reason table](analysis.md#exit-reasons-and-next-actions). The errors name the reason, the field and the source and never repeat the value.

| Reason | Raised for |
| --- | --- |
| `loop_declaration_invalid` | a declaration that is not one object, or a spec with an unsupported profile or incomplete sources |
| `loop_field_unknown` | a field outside the eight declared ones |
| `loop_authority_refused` | an authority key in any source |
| `loop_accept_score_invalid` | a score that is not a number in (0, 1], or an empty or invalid map |
| `areas_not_declared` | a per-area map with no declared areas |
| `loop_area_undeclared` | a per-area key that is not a declared area |
| `loop_accept_basis_invalid` | a basis other than `lower_bound` |
| `loop_margin_invalid` | a margin outside [0, 1) |
| `loop_max_rounds_invalid` | `max_rounds` outside 1 to 10 |
| `loop_plateau_rounds_invalid` | `plateau_rounds` below 1 or above `max_rounds` |
| `loop_on_regression_invalid` | `on_regression` other than `stop` or `rollback_continue` |
| `loop_alpha_invalid` | `alpha` outside (0, 1) |
| `loop_limits_invalid` | project limits that are not positive |
| `loop_order_violation` | an event the table does not allow |
