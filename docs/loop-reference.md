# Loop declaration, state machine and controller reference

This page is the reference for the acceptance score declaration, the
evaluate > improve > evaluate state machine and the loop controller in
`agent_harness_lab.loop`. The declaration and the machine are pure: no model call,
clock, network or file access beyond reading configuration. The controller drives
the machine through two injected ports and writes only its private round ledger.
Nothing calls the controller yet: `ahl improve` still makes one pass, and no
command, receipt or exit code changes. Adapters that wrap the real evaluation come
in a later slice.

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
| `on_regression` | What a regression does; `rollback_continue` applies only to a regression beyond `regression_margin`, never to a critical or per-area one | `stop` | `stop` or `rollback_continue` | `loop_on_regression_invalid` |
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
4. An unknown `regression` gives `evidence_insufficient`. A regression gives `regressed` and `regression`, unless `on_regression` is `rollback_continue`, the regression is `margin`, the cause is `candidate` and a mechanism is `available`: then the limits apply and the outcome stays `regressed`. A `critical` or `area` regression always stops.
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

## Loop controller

`agent_harness_lab.loop.controller.run(spec, evaluator, proposer, ledger, clock)`
runs one loop and returns its result. It calls `machine.step` for every move and
`machine.decide` for every decision, and nothing else decides. It adds no optimizer
and no statistics: the evaluator supplies each score as an interval. It imports
nothing from `workflow.py` and makes no model, subprocess or network call, and its
time comes from the injected `clock`. It writes only the ledger, never a project
file, and the result states `recommendation_only: true`, `automatic_promotion:
false` and `certification: unverified`.

### Ports

| Port | Members | Contract |
| --- | --- | --- |
| Evaluator | `frozen_inputs`, `evaluate(snapshot, frozen_inputs)` | `frozen_inputs` is the SHA-256 of the frozen bank, graders, seed schedule and runtime. `evaluate` returns an `Evaluation` for the snapshot. |
| Proposer | `baseline`, `mechanisms`, `propose(best_snapshot, mechanism)` | `baseline` is the SHA-256 of the starting snapshot and `mechanisms` the queue in priority order. `propose` returns a `Candidate` (a snapshot digest and the mechanism it addresses) staged outside the project, or `None` when it cannot stage one. |
| Clock | a callable | Finite monotonic seconds. |

An `Evaluation` holds `point`, `lower` and `upper`, an `evidence` state (`scored`,
`provisional`, `no_evidence` or `blocked`), a `vacuity` label or `None`, per-area
lower bounds in `areas`, a `critical` flag, a regression `cause`, the `trials` and
`seconds` it spent, and the `inputs` digest it ran on. An evaluation is `valid`
only when it is `scored`, has no vacuity label and its interval is sound: finite
numbers with `0 <= lower <= point <= upper <= 1`, and area bounds inside `[0, 1]`.
A listed vacuity label gives `vacuous`; any other doubt gives `incomplete`. An
evaluation whose `inputs` is a different digest than the frozen one is
`comparison_invalid`; one that states none is `incomplete`, never valid. A port
that returns something outside this contract raises `loop_port_invalid` naming the
field, before the ledger is created when the defect is in a port's declared
attributes.

### A round

1. The baseline is evaluated on the frozen inputs. A `valid` baseline that meets the
   bar stops `accepted_without_change` with no proposer call; one that does not
   continues.
2. The first pending mechanism in the queue is offered to `propose` with the best
   snapshot. A mechanism for which `propose` returns `None` is `deferred` and not
   offered again; with none left the loop stops `no_actionable_mechanism`.
3. The candidate is recorded, evaluated on the same frozen inputs, and decided by
   `decide`. A `valid` candidate with a supported improvement over the best becomes
   the new best and resets the plateau streak. A `regressed`, `inconclusive` or
   unmet candidate is kept in the ledger as evidence and never becomes the base.

The best snapshot starts as the baseline. The next round always proposes from it,
so after a regression under `rollback_continue` the base digest passed to the
proposer is the best snapshot and not the regressed candidate. The acceptance bar
is read on the interval lower bound: an absolute `accept_score` is met when
`lower` reaches it; in relative mode a candidate meets it when its interval clears
the round-0 baseline by `noise_margin`, and the baseline never meets it; a
per-area bar is not decided by this slice and is unknown, so it stops
`evidence_insufficient`. An improvement is supported when `lower` is above the
best point value plus `noise_margin`; a regression is a critical flag, an area
bound below its best by more than `regression_margin`, or a point value below the
best by more than `regression_margin`. A candidate that omits an area the best
scored is unknown.

### Mechanism dispositions

Each mechanism in the queue is `pending` until it is used once, then keeps one
disposition for the run: `accepted` or `improved` for the result it produced,
`rejected` after a regression, `inconclusive` after noise or incomplete evidence,
`evaluating` while its candidate is evaluated, or `deferred` when no candidate
could be staged. A repeated mechanism id is one mechanism. The result and the
`stopped` event list every disposition. Rounds count independent mechanisms and
observed outcomes, not edits.

### Budget reserve

Before every improve step the controller checks that what is left pays for
re-evaluating a candidate. The reserve is the baseline evaluation's recorded
`trials` and `seconds`; the time left is the spec's `limits.seconds` minus the
injected clock's elapsed seconds, and the trials left are `limits.trials` minus the
trials the evaluations reported. An evaluation that reports no usage is charged,
and when it is the baseline reserved, the whole evaluation allotment: the trials
stage's 40 percent share of each ceiling (see [stage budgets](budget-stages.md)),
and `usage.unknown_usage_evaluations` counts them. If the reserve is not left the
loop stops `budget_exhausted` with `stop_reason: reserve_for_evaluation_unavailable`
before the proposer is called, so every improvement that starts can be evaluated.

### Result

| Field | Meaning |
| --- | --- |
| `profile` | `ahl-loop-result-v1` |
| `spec_sha256`, `frozen_inputs_sha256` | The declaration digest and the frozen inputs every evaluation ran on |
| `mode`, `acceptance_source` | `absolute`, `relative_supported_improvement` or `per_area`, and where the bar came from |
| `looks`, `per_look_alpha` | The number of looks at the frozen bank (`max_rounds`) and the error level of each, `alpha / max_rounds` |
| `outcome`, `stop`, `stop_reason` | The last outcome, the one stop criterion, and `reserve_for_evaluation_unavailable` for `budget_exhausted` |
| `rounds` | Rounds started |
| `best` | The best snapshot digest and its evaluation summary |
| `mechanisms` | Each mechanism's disposition |
| `usage` | `trials`, `seconds` and `unknown_usage_evaluations` |
| `ledger` | The number of events and the SHA-256 of the last one |
| `recommendation_only`, `automatic_promotion`, `certification` | `true`, `false` and `unverified` |

An evaluation summary keeps its numbers only when the interval is sound, so a
malformed score is still recorded without breaking the ledger. An interrupt (a
`KeyboardInterrupt`) stops the run `interrupted` and records it, unless nothing was
recorded yet, when it propagates. Any other exception propagates and leaves a
ledger that replays without a `stopped` event.

### Round ledger

`LoopLedger(root, project_root)` is the `private_journal.PrivateJournal` pattern
that `campaigns/store.py` uses, with no new ledger format: one owner-only case
directory under `root`, one atomic file per event chained to the one before, and a
frozen `spec.json`. `root` must be a new owner-only directory outside the project
on real directories, otherwise `loop_ledger_invalid`. `recorded_at` is the elapsed
seconds on the injected clock, so two runs of one script write byte-identical
ledgers. A ledger holds ids, digests, verdicts and numbers, never task, grader,
candidate or captured text.

| Event | Follows | Payload |
| --- | --- | --- |
| `spec_frozen` | none | declaration digest, frozen inputs, mode, looks, per-look alpha, mechanism queue |
| `baseline_evaluated` | `spec_frozen` | verdict, snapshot, evaluation summary |
| `round_started` | `baseline_evaluated`, `decided` | round, mechanism, base snapshot |
| `candidate_frozen` | `round_started` | round, candidate snapshot, mechanism |
| `candidate_evaluated` | `candidate_frozen` | verdict, snapshot, evaluation summary |
| `decided` | `candidate_evaluated` | outcome, mechanism, its disposition, best, acceptance, regression and improvement |
| `stopped` | any other event | stop criterion, reason, rounds, best, dispositions |

A ledger holds at most `4 * max_rounds + 8` events, above anything a run within
`max_rounds` writes; one more is `loop_ledger_full` and writes nothing. An event
out of this order is `loop_ledger_invalid`, as is a missing, extra, edited or
foreign file when the ledger is read. `replay(spec)` feeds the machine events back
through the state machine and returns the final `Run`; it refuses a ledger
recorded under another declaration. An append that crashes before it is published
leaves no event and no temporary file, so a ledger stays replayable at every
prefix.

## Simulation test-bed

`tests/support/loop_sim.py` is a deterministic fake runtime and fake graders for
testing the whole loop with no model, subprocess, sleep, network or real clock.
It is test support, not product code: it adds no command and changes no receipt.

A `Script` is a table of scripted evaluations keyed by round (round 0 is the
baseline), the mechanisms the scripted proposer offers (each once, in order), a
declaration, the `trials` and `seconds` limits and a `seed`. Each `Evaluation`
holds a point score with its interval, an evidence state (`scored`,
`provisional`, `no_evidence` or `blocked`), an optional vacuity label, per-area
lower bounds, a critical flag, a regression `cause`, its usage and the digest of
the frozen inputs it ran on. A `Meter` is the injected clock and trial counter,
so a budget is exact. `ScriptedEvaluator` and `ScriptedProposer` implement the
`Evaluator` and `Proposer` protocols a controller consumes, and the bed does
not import `workflow.py`.

`drive(script, base)` runs a script through the real controller over `ScriptedPorts`
and a `LoopLedger` under `base`, and returns the result, the ledger and the ports.
The k-th evaluation of a run is the script's round k, so a script that runs out of
rounds fails with a `LookupError` instead of inventing a score. The bed's
`Evaluation` and vacuity labels are the controller's own.

`play(script)` is the lighter stand-in that predates the controller: it runs the script and drives `loop.machine.step` for every event,
so an illegal order raises `loop_order_violation`. It returns a trace, and
`canonical(trace)` is sorted compact JSON that `replay(spec, text)` feeds back
through the machine. The same script and seed give byte-identical traces. `play`
has its own `classify` and acceptance for the validity gate: an evaluation
is `valid` only when it is `scored` and has no vacuity label, and any other
evidence state or label is `vacuous` or `incomplete`, never `valid`. In `play`, per-area
and relative acceptance bars are not simulated and resolve to unknown, so a
script that declares one stops `evidence_insufficient` rather than accepting; the
controller decides relative mode itself and leaves only the per-area bar unknown.

To write a scenario, build the script and assert its trace:

```python
script = build('plateau_after_gain', [0.5, 0.65, 0.65, 0.65], declaration=dict(max_rounds=5))
trace = play(script)  # outcome, stop, rounds, best, trials, seconds, calls and the event list
assert (trace['stop'], trace['best']) == ('plateau', 'cand-1')
```

A number in `build` is a point score with a half-width of 0.05; pass `score(...)`
for any other field. Give a vacuous evaluation a score that would meet the bar,
so that only the vacuity branch can stop it. These presets are in `PRESETS`:

| Preset | Scenario | Outcome and stop |
| --- | --- | --- |
| `baseline_already_accepted` | The baseline meets the bar | `accepted`, `accepted_without_change` |
| `converges_in_k_rounds` | Each round improves until round `k` meets the bar | `accepted`, `acceptance_score_reached` |
| `regresses_on_round_n` | Round `n` falls below the best; `on_regression` may roll back | `regressed`, `regression` |
| `regresses_critical_area` | A met bar with a critical flag or a fallen area | `regressed`, `regression` |
| `plateaus` | Rounds equal to the baseline | `inconclusive`, `plateau` |
| `noisy_tie` | Seeded noise inside the noise margin | `inconclusive`, `plateau`, never `improved` |
| `vacuous_baseline`, `vacuous_candidate` | One of the five vacuity labels on a score that meets the bar | `inconclusive`, `evidence_insufficient` |
| `budget_exhausted_before_reeval` | The budget cannot cover another evaluation | `budget_exhausted` |
| `no_mechanism_left` | The proposer has no mechanism left | `no_actionable_mechanism` |
| `grader_changed_between_arms` | The candidate ran on other frozen inputs | `inconclusive`, `comparison_invalid` |
| `interrupted_after_each_event` | The run is interrupted after `after` events | `interrupted`, with the last recorded outcome |

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
| `loop_port_invalid` | an evaluator, proposer, candidate, evaluation or clock outside its port contract |
| `loop_ledger_invalid` | a ledger root inside the project or on a link, an event out of order, a reused ledger, or a tampered or incomplete ledger read back |
| `loop_ledger_full` | a ledger event beyond `4 * max_rounds + 8` |
