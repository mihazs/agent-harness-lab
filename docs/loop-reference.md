# Loop declaration, state machine and controller reference

This page is the reference for the acceptance score declaration, the
evaluate > improve > evaluate state machine and the loop controller in
`agent_harness_lab.loop`. The declaration and the machine are pure: no model call,
clock, network or file access beyond reading configuration. The controller drives
the machine through two injected ports and writes only its private round ledger.
The [frozen-bank evaluator](#frozen-bank-evaluator) adapts the real evaluation to
the controller's evaluator port. `ahl improve` calls the controller by default
whenever the project pins a task bank, with or without a
[loop flag or declaration](#running-the-loop-from-ahl-improve); `--single-pass` and a run
that has no frozen bank to drive keep the earlier single pass and say so in a `loop`
block that reads `not_used`. How to run it is in
[improve until an acceptance score is reached](improve-until-score.md) and why it is
shaped this way in [why the loop evaluates, improves, then evaluates](loop-rationale.md).

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
| Evaluator | `frozen_inputs`, `evaluate(snapshot, frozen_inputs)`, optional `investigate(best_evaluation, candidate_evaluation)`, optional `settle(snapshot)` | `frozen_inputs` is the SHA-256 of the frozen bank, graders, seed schedule and runtime. `evaluate` returns an `Evaluation` for the snapshot. `investigate` is asked about a regression only, against the evaluation of the best snapshot, and returns `candidate`, `other` or `None` for who caused it. `settle` is asked only by [a resume](#resuming-a-loop) about an evaluation that was running when the run stopped. |
| Proposer | `baseline`, `mechanisms`, `propose(best_snapshot, mechanism)` | `baseline` is the SHA-256 of the starting snapshot and `mechanisms` the queue in priority order. `propose` returns a `Candidate` (a snapshot digest and the mechanism it addresses) staged outside the project, or `None` when it cannot stage one. |
| Clock | a callable | Finite monotonic seconds. |

An `Evaluation` holds `point`, `lower` and `upper`, an `evidence` state (`scored`,
`provisional`, `no_evidence` or `blocked`), a `vacuity` label or `None`, per-area
lower bounds in `areas`, the `regressed_areas` the per-area gate found (a scored area that lost paired outcomes beyond the regression margin; see the [area gate reference](area-gate-reference.md)), a `critical` flag, a regression `cause` (used only when the evaluator has no `investigate`), the `trials` and
`seconds` it spent, the `inputs` digest it ran on, and, on the baseline only, a `reserve_trials` and `reserve_seconds`: what
re-evaluating a candidate will cost when that is more than the baseline spent (a paired run costs two arms). The budget
reserve is the baseline's declared reserve when it is a valid count or finite number of seconds, else what it spent. An evaluation is `valid`
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
scored is unknown. Whether a regression rolls back is decided by who `investigate`
blames: only `candidate` lets `rollback_continue` go on, and `other`, `None` and any
other answer stop the loop. [`FrozenBankEvaluator.investigate`](#regression-and-the-investigation-artifact)
answers from the real investigation artifact; the simulation test-bed answers with
the cause its script gives.

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
| `outcome`, `stop`, `stop_reason` | The last outcome, the one stop criterion, and its reason: `reserve_for_evaluation_unavailable` for `budget_exhausted`, `unresolved_request` for an `interrupted` resume |
| `rounds` | Rounds started |
| `best` | The best snapshot digest and its evaluation summary |
| `mechanisms` | Each mechanism's disposition |
| `usage` | `trials`, `seconds` and `unknown_usage_evaluations` |
| `ledger` | The number of events and the SHA-256 of the last one |
| `resumed` | Only for a run that was resumed: `count` of resumptions and `abandoned_rounds` |
| `recommendation_only`, `automatic_promotion`, `certification` | `true`, `false` and `unverified` |

An evaluation summary keeps its numbers only when the interval is sound, so a
malformed score is still recorded without breaking the ledger. It also records the
`inputs` digest the evaluation ran on, or `null` when that is not a digest, so the
equality of the frozen inputs across evaluations can be read from the ledger. An interrupt (a
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
| `spec_frozen` | none | declaration digest, frozen inputs, mode, looks, per-look alpha, mechanism queue, and `carried` dispositions when an earlier run handed any on |
| `baseline_started` | `spec_frozen`, `resumed` | durable marker and snapshot before dispatching a journaled baseline |
| `baseline_evaluated` | `spec_frozen`, `baseline_started` | verdict, snapshot, evaluation summary |
| `proposal_started` | `baseline_evaluated`, `decided`, `resumed` | durable marker before invoking a journaled proposer |
| `round_started` | `baseline_evaluated`, `decided`, `proposal_started` | round, mechanism, base snapshot, every mechanism's disposition |
| `candidate_frozen` | `round_started` | round, candidate snapshot, mechanism |
| `candidate_started` | `candidate_frozen`, `resumed` | durable marker and snapshot before dispatching a journaled candidate evaluation |
| `candidate_evaluated` | `candidate_frozen`, `candidate_started` | verdict, snapshot, evaluation summary |
| `decided` | `candidate_evaluated` | outcome, mechanism, its disposition, best, every disposition, acceptance, regression, improvement and the `stop` the decision reached (or `null` to go on); `abandoned: true` for a round no evaluation completed |
| `stopped` | any other event | stop criterion, reason, rounds, best, dispositions, last outcome |
| `resumed` | any event, `stopped` only when it was `interrupted` | events held, previous head, trials charged for the evaluation that was running, and whether it was `unresolved` |

A ledger holds at most `6 * max_rounds + 9` events, above anything a run within
`max_rounds` writes; one more is `loop_ledger_full` and writes nothing. A ledger that is
reopened for a resume allows three more events for each of at most ten resumes. Beside the
events, `head.json` keeps the count and SHA-256 of the newest event, because no later
event links to it, and `resume.lock` is the claim a run or resume holds on the case. An event
out of this order is `loop_ledger_invalid`, as is a missing, extra, edited or
foreign file when the ledger is read. `replay(spec)` feeds the machine events back
through the state machine and returns the final `Run`; it refuses a ledger
recorded under another declaration. An append that crashes before it is published
leaves no event and no temporary file, so a ledger stays replayable at every
prefix.

### Resuming a loop

`controller.resume(spec, evaluator, proposer, ledger, clock, spent=0.0, dispositions=None)`
continues a run from the last durable event of its ledger. `ledger` is a new
`LoopLedger` over the store of the run, and the declaration, the frozen inputs, the
baseline, the mechanism queue and the carried dispositions must be the ones the run
was frozen with. Only a completed evaluation is durable, so resume replays the
recorded events, rebuilds the controller's position from them and, subject to the
request reconciliation checks below, goes on:

| Last durable event | A resume |
| --- | --- |
| a `stopped` that is final | returns the recorded result, with no port call and no new event |
| `spec_frozen` | starts the baseline when the dispatch journal proves it did not start; otherwise requires settlement |
| `baseline_started` | requires settlement before any retry |
| `baseline_evaluated` | takes the decision on the baseline and goes on |
| `proposal_started` | requires reconciliation before any retry |
| `round_started` | requires proposal reconciliation when dispatch may have happened |
| `candidate_frozen` | evaluates the frozen candidate when the dispatch journal proves evaluation did not start; otherwise requires settlement |
| `candidate_started` | requires settlement before abandoning the incomplete round and starting a fresh one |
| `candidate_evaluated` | takes the decision on the recorded evaluation |
| `decided` | goes on to the next round or records the stop the decision reached |
| `stopped` with `interrupted`, or none | continues from the event before it |

The ledger is verified before anything runs: the chain, the order of the events
through the state machine, the declaration digest (`resume_spec_changed`) and the
newest event against `head.json` (`loop_ledger_invalid`). A run that is alive, or
another resume, holds the case (`resume_busy`). A resumed ledger records each
resumption as a `resumed` event; a stop that a `resumed` event follows was only an
interruption, and a `resumed` event after any other stop is refused.

The stored spec's identity is recomputed and checked against its digest and the
first ledger event. Returning a finished receipt also requires its spec to match
the stored spec, its stop criterion to match the final event, and its outer status,
reason, output and authority fields to agree with that criterion. Baseline and
candidate evaluations, decisions, the best result, frozen input digest, stop reason
and mechanism dispositions must also match the verified events. Paired statistics,
budget details and other fields absent from the ledger are not verified by this check.
Source labels are consistent within the document but excluded
from the identity digest, so coordinated changes to those labels in both copies
are not authenticated by this check. The unkeyed ledger hashes detect inconsistent
records; they do not authenticate a writer who can rewrite the whole ledger.

An evaluation that was running when the run stopped is never a completed one. Its
round is recorded `abandoned`: no score, a decision `inconclusive` with the
disposition `abandoned`, one round counted against `max_rounds` and the plateau
streak, and its mechanism goes back to `pending` because nothing was learned about it.
Spent budget stays spent: the clock continues from the later of `spent` and the last
recorded time, and the trials `evaluator.settle(snapshot)` reports are charged in the
`resumed` event, though never counted as a result. `settle` returns the trials the
evaluation consumed when every request it sent has an outcome, `0` when none started,
and `None` when one may not have. With no `settle`, or `None`, the evaluation is not
repeated by itself: the run ends `interrupted` with `stop_reason: unresolved_request`
and the receipt's remediation names the reconcile step. A later controller resume asks
the evaluator again; it can continue only if the evaluator supplies a settled result.
The built-in CLI evaluator currently has no settlement port, so external reconciliation
alone does not let `ahl improve --resume` continue an unresolved evaluation. The built-in
proposer likewise has no reconciliation port for an already-dispatched proposal.
A proposal may also have been
dispatched after the last durable baseline or non-final decision. The controller does
not ask again unless the optional proposer port `proposal_retry_safe(events)` verifies
from durable evidence that retrying cannot repeat an unresolved or unrecorded request.
The controller trusts only its exact `True` result; a missing port or any other result
ends `interrupted` with `unresolved_request`. A proposal lost before its candidate was
frozen is also ambiguous. New runs using the built-in proposer freeze the journal protocol and write `proposal_started`
before entering it. If a verified new ledger stops at a baseline or non-final decision
without that marker, no proposal was dispatched in that phase. A marker still requires
reconciliation; legacy ledgers with no protocol cannot treat its absence as proof.
The command-line path can reconstruct new cooperative loop runs from their frozen context;
the marker is neither a completed proposal nor a trial. Older runs without a context are
not automatically reconstructed.

New cold-resumable runs also record `baseline_started` and `candidate_started` before
invoking each evaluator. A checkpoint before the relevant marker can start that
evaluation; after it, an unsettled evaluation remains interrupted. Older ledgers
cannot use the absence of these markers as proof that no request was sent.

`ahl improve PROJECT --resume RUN_ID` verifies the private context against the ledger,
the current project, frozen bank, scoring manifests, runtime executable and toolkit.
It restores completed observations and stage spend, and charges time since the last
durable event against the time limit. A missing or changed input blocks before a new
request. A resumed baseline needs enough remaining trials and a positive time balance; a frozen candidate
also needs its recorded round reserve. It does not authenticate a writer who can rewrite
the whole private ledger. The executable and evaluation-runner checks hash those files;
they do not freeze an executable's interpreter, shared libraries, environment or provider model.

A project lock recorded on a previous Linux boot can be reclaimed after a reboot;
an older lock without a boot identity or a lock from the current boot remains busy.
A same-boot child may outlive its parent, so verify and reconcile it before removing
a stale lock. Other platforms retain manual stale-lock recovery.

When request reconciliation permits continuation, a resumed run reaches the stop
criterion the uninterrupted run reaches and charges the same trials and seconds,
apart from the abandoned round it adds; `usage` never counts unrecorded work twice.

## Frozen-bank evaluator

`agent_harness_lab.loop.adapters.FrozenBankEvaluator` implements the controller's
evaluator port over the existing trial runner and `evaluation/statistics`. It adds
no optimizer and no statistics engine, and like the controller it only recommends:
nothing here applies, promotes or certifies a candidate. `ahl improve` builds it over
the pinned bank when the loop is on.

```python
evaluator = FrozenBankEvaluator(manifest, runner, snapshots, work, spec=spec, min_roots=2)
result = controller.run(spec, evaluator, proposer, ledger, clock)
```

| Argument | Meaning |
| --- | --- |
| `manifest` | The bank's evaluation manifest. Only its development and regression tasks are kept. |
| `runner` | `ExecutorRunner(run_evaluation.evaluate, agent, binary)`, or anything with `runtime_sha256()` and `run(arms, snapshots, manifest, out)` that returns the `results.json` shape. |
| `snapshots` | A callable from a snapshot digest to its directory. |
| `work` | An owner-only directory outside the bank and the snapshots; each run writes `evaluation-N/` and each investigation `investigation-N.json` there. |
| `spec` | The resolved `LoopSpec`: its per-look alpha, `regression_margin` and `noise_margin` bound every interval and paired comparison. |
| `min_roots` | The plan minimum of independent roots, 2 by default. |

### What is frozen

The constructor writes `loop-scoring-manifest.json` beside the bank's manifest: the
tasks whose split is `development` or `regression`, with their graders and controls.
A task with any other split (`heldout`, `transfer`, `certification`, a missing or
unknown one) is counted in `excluded` and is never run, hashed, graded or read, so
no held-out or certification input is opened to choose a candidate. A manifest that
declares `protected_access` or a certification plan is refused with
`loop_port_invalid` on the field `bank`, and so is one that cannot be read or has
duplicate or non-text task ids. An `experiment` or `cohort_manifest` declaration never
reaches the scoring manifest. The file is
written once; a bank that would freeze to other bytes is refused.

`frozen_inputs` is the identity of five facts: the bank (the task content and the
digest of every fixture), the grader (`grader_fingerprint`), the seed schedule, the
per-arm budget share (the number of scoring tasks) and the runtime (the agent's launch
configuration and executable content). The evaluator reuses the executable hash while
its file identity, size and change timestamps are unchanged; an observed change during
an evaluation leaves that evaluation's inputs unknown even if the original bytes return.
The recovery checkpoint and reconstructed evaluator reuse that checked hash while the
file metadata remains unchanged, instead of reading the executable again.
Older launch-only runtime
identities are not comparable with this version. The evaluator observes the facts before and
after every run and reports the digest it observed as `Evaluation.inputs`, so a
difference from the frozen digest is `comparison_invalid` in the controller, and a
fact that cannot be read, or an input observed to move during the run, leaves `inputs`
unknown, which is `incomplete`. When the inputs already differ before a run, no trial
is spent: the evaluation reports no trials and the evidence `blocked`.

### One arm first, a fresh paired run after

The first evaluation scores the baseline alone, so it costs one arm (the trial runner
takes `arms=('baseline',)`). Every later evaluation is a fresh paired run of the best
snapshot against the candidate, so a round costs two arms, and no trial of an earlier
run is reused as fresh execution. The best snapshot is the one the proposer staged the
candidate from: the proposer records it in `evaluator.bases[candidate]`, and the first
evaluated snapshot is the default. The baseline evaluation declares `reserve_trials`
as twice the number of scoring tasks, so the controller starts a round only when two
arms are still affordable.

### The absolute score

The score of an arm is the mean of per-root mean outcomes in [0, 1], where a task
counts only with exactly one row that ended `passed` (1) or `failed` (0), and a root
counts only when all of its tasks do. The interval is `statistics.bound` over those
roots with the loop's per-look alpha, cut to [0, 1]. Its radius is derived for
differences in [-1, 1], so on [0, 1] it is valid but conservative; a tighter bound
needs its own derivation and is not part of this slice. The radius shrinks with the
square root of the number of roots: at alpha 0.05 it is 0.27 for 100 roots and 0.78
for 12, so a small bank rarely supports an improvement.

| Evidence | When | Effect |
| --- | --- | --- |
| `scored` | at least `min_roots` roots, none unknown | can be valid |
| `provisional` | fewer than `min_roots` roots, or any root with an unknown outcome | `incomplete`: the baseline cannot be accepted and a candidate cannot improve |
| `no_evidence` | no root counts | the interval is [0, 1]; `incomplete` |
| `blocked` | the inputs already differed, so no trial ran | `comparison_invalid` or `incomplete` |

An outcome that is not `passed` or `failed` (an execution error, a budget stop, an
unknown trajectory, a missing row, two rows for one task) leaves its root unscored.
It is never counted as a pass or a fail, and a root left out never raises the score:
any unscored root keeps the evaluation `provisional`.

### The validity gate

The evaluation labels itself with `evaluation.vacuity.classify` over the rows of the
arm it scored, with the best snapshot and the evaluated snapshot as the arm digests
of a paired run (a baseline-only run has no second arm, so `arms_identical` is not
applied). `Evaluation.vacuity` is the first label in the closed-set order; the other
labels are in `evaluator.runs[-1]['labels']`.

| Label | A synthetic bank that triggers it | In the loop |
| --- | --- | --- |
| `no_eligible_cases` | every task is outside the scoring partitions; the runner is never called | baseline: `evidence_insufficient` with no improvement and no trial spent |
| `zero_trials` | the runner returns no row for the arm | as below |
| `all_outcomes_unknown` | every row ends in an execution error | as below |
| `grader_not_executed` | rows end `passed` or `failed` with no recorded grader execution | as below |
| `arms_identical` | the candidate is the best snapshot | candidate only |

A vacuous baseline stops the loop `evidence_insufficient` before any improvement is
proposed. A vacuous candidate is `inconclusive`: it is recorded with the mechanism's
disposition `inconclusive`, never becomes the best and can never be accepted.

### Regression and the investigation artifact

When the project declares areas, each paired run is also gated per area (`evaluation.area_gate`): a non-empty `regressed_areas` is the `area` regression, which stops the loop with `regression` whatever the policy or the pooled score, and the gate block is kept in `evaluator.runs[i]['area_gate']` and reported as an `area_gates` entry of the run. The stop keeps the reason `loop_regression`, never `area_regressed` (which is the `--single-pass` outcome), and its receipt `remediation` names `regressed_areas` and `area_gates` instead of the generic regression step.

Each paired run also goes through `evaluation.before_after.decide`, with the loop's
regression and noise margins, against the frozen identity. Its block is kept in
`evaluator.runs[i]['guard']`: `candidate_regressed` with `adoption_blocked: true` when
the paired delta is below the negative margin, `improved`, or `inconclusive`. The
controller decides from the absolute intervals as before; the guard feeds the
investigation. When the controller asks `investigate(best, candidate)` about a
regression, the evaluator writes `investigation-N.json` (per-task and per-area deltas,
the flipped trials, what changed between the arms and a classified cause) and answers
`candidate` for `candidate_caused`, `other` for `pipeline_caused` and `None` for
`undetermined`. It is asked only about an evaluation that ran on the frozen inputs, so
there the grader and inputs are known to be unchanged.

A pipeline-caused regression cannot reach that question: a change of the grader, the
bank or the runtime changes the observed digest, so the loop ends `comparison_invalid`
with no verdict and no `decided` event. The evaluator then writes the same artifact
with the cause `pipeline_caused` and the changed fact in `comparison.mismatched`, and
its `next_step` names the AHL defect so it gets a regression test.

### First use

`FrozenBankEvaluator.first_use(construct, runner, snapshots, work, spec=spec, stages=stages)`
calls `construct()` once, charged to the construction stage of the budget (`stages`,
see [budget stages](budget-stages.md)), freezes the manifest it returns, and sets
`constructions` to 1. The evaluator keeps no reference to `construct`, so no later
round can rebuild the evaluation: every round scores on that bank.

### The trial runner's arms

`ExecutorRunner.run` passes `arms` to `run_evaluation.evaluate`, which runs only the
named versions (`baseline`, `candidate` or both, each at most once; a repeated plan
needs both). Without `arms` it runs both, so every other caller is unchanged.

## Running the loop from `ahl improve`

`ahl improve` and `ahl skills invoke` (one shared parser and receipt) run the loop
by default. This is a breaking change of the command: a run with no loop flag and no
loop declaration is a loop run whenever `ahl.json` pins a task bank, with the documented
defaults labelled `acceptance_source: default` and the mode `relative_supported_improvement`
(`--max-rounds 1` makes exactly one evaluate, improve, evaluate pass). The previous
improve-first single pass stays reachable for one release as `--single-pass`; see
[the default and `--single-pass`](#the-default-and---single-pass).

### Declaring the loop

A run declares its bar and stop policy with any of these; none is required:

| Source | How |
| --- | --- |
| `--accept-score X` | Stop when the lower bound of the frozen development and regression success rate reaches `X`, a number above 0 and at most 1. Without it the loop stops on a supported improvement over its own baseline and labels the bar `acceptance_source: default`. |
| `--max-rounds N` | At most `N` improvement rounds, a whole number from 1 to 10 (default 3). |
| `--plateau-rounds N` | Stop after `N` consecutive rounds without a supported improvement, from 1 up to `--max-rounds` (default 2, or `--max-rounds` when that is smaller). |
| `--on-regression stop\|rollback_continue` | What a candidate-caused regression within its margin does (default `stop`); a critical or per-area regression always stops. |
| A `loop` object | In the project `ahl.json` or in the user file beside the remembered runtime. A flag overrides the user file, which overrides the project file, which overrides the default; the receipt names the source of every value. |

Every value is checked before anything runs, with the reasons in
[declaration fields](#declaration-fields): an invalid flag, project or user value exits
2 with its own reason (for example `loop_max_rounds_invalid`), the stage `usage` and one
next step. A flag value is read as text, so `--max-rounds 2.5`, `--accept-score nan` and
an empty value are the same typed refusal. `--repair-contracts` and `--resume` do not
combine with the loop options. A declaration sets a bar and never authority: a `loop`
object with `scope`, `apply`, `recommendation_only`, `automatic_promotion`,
`promotion` or `certification` is `loop_authority_refused`, and the run stays
recommendation-only whatever it declares.

### The default and `--single-pass`

| Run | What happens | `loop` block |
| --- | --- | --- |
| No flag, no declaration, a pinned bank | The loop runs with the defaults (`max_rounds` 3, `plateau_rounds` 2, `on_regression` `stop`, `alpha` 0.05) and no absolute bar | The full receipt, `acceptance_source: default`, `mode: relative_supported_improvement` |
| A flag or a declaration, a pinned bank | The loop runs with the declared values | The full receipt, the source of each value named |
| A flag or a declaration, no pinned bank or a controlled runtime | `blocked` with `loop_requires_frozen_bank`, exit 3, nothing ran | `status: not_started` |
| A loop run that a gate stopped before its first evaluation (no outcome evidence, no matching history, an unavailable sandbox) | `blocked` with that gate's own reason; no model call that cannot succeed is made | `status: not_started`, `reason` the gate's reason |
| No flag, no declaration, no pinned bank or a controlled runtime | The single pass runs | `status: not_used`, `reason: loop_requires_frozen_bank` |
| No flag, no declaration, `--experiment-request` (a request needs the starter bank, so no bank is pinned) | The fixed-bank comparison of the request runs | `status: not_used`, `reason: loop_requires_frozen_bank` |
| `--single-pass` | The previous improve-first single pass runs | `status: not_used`, `reason: single_pass_requested`, `deprecated: true` |
| `--repair-contracts` | The deterministic repair runs; it is no improvement pass | no `loop` block |

A `not_used` block holds `profile`, `status`, `reason`, `single_pass: true`, `deprecated`
(true only for `--single-pass`), `recommendation_only: true`, `automatic_promotion: false`,
`certification: unverified` and one `remediation` step, and it is the same on `ahl improve`
and `ahl skills invoke`. `--single-pass` is removed one release after the loop default and
does not combine with `--accept-score`, `--max-rounds`, `--plateau-rounds` or
`--on-regression` (exit 2, `invalid_usage`); it wins over a project or user `loop`
declaration, which it does not read. `ahl watch` has no loop options and keeps its single pass.

### What a loop run needs

The loop evaluates every round on one frozen bank, so it needs a pinned task bank in
`ahl.json` (`bank.path` and `bank.sha256`, with development, calibration and
certification or transfer tasks) and the cooperative runtime the configuration names.
A run that asked for the loop with a flag or a declaration and has none is `blocked` with
reason `loop_requires_frozen_bank`, exit 3, a `loop` block that says `status: not_started`,
and one remediation step; it never falls back to a single pass. A run that did not ask
makes the labelled single pass instead (see [the table above](#the-default-and---single-pass)).
The frozen bank is the pinned one: first-use construction of a bank and
`--experiment-request` banks are not available to the loop in this release.

Only the development and calibration (regression) tasks are scored. The certification
and transfer (held-out) tasks are never run, hashed or read to choose a candidate.

### What a run does

1. The existing evidence and preflight stages run, then the baseline is evaluated on
   the frozen bank with one arm (the baseline's own score, with its interval).
2. A baseline that meets the bar stops `accepted_without_change` with no preparation
   call. Otherwise each round starts only if what is left of the budget also pays for
   re-evaluating its candidate.
3. A round offers one mechanism to the preparation stage, in this order: `focus` when
   `--focus` or an audit hypothesis was given, each distinct mechanism of the analysis
   brief, then `agent_selected`, which lets the agent discover one. The agent edits a
   staged copy of the best snapshot only, and the candidate must pass what a single
   pass requires: the configured scope, instruction and rule validation, agent
   definition and skill review, no change to the project during preparation, and no
   active guidance removed (a removal needs trial records the loop does not give the
   gate). A mechanism that cannot give such a candidate is deferred and not offered
   again, with one closed reason in `loop.deferrals`.
4. The frozen copy of the candidate is evaluated in a fresh paired run against the best
   snapshot on the same frozen inputs. A different digest ends the run
   `comparison_invalid`.
5. The round is decided by the [decision table](#decision-table). The best snapshot
   moves only for an `accepted` or `improved` candidate; a regressed, inconclusive or
   unmet candidate stays in the receipt as evidence.

The time ceiling is the run's `--timeout` and the trial ceiling is the shared ceiling
minus one preparation role for each round the declaration allows; the seconds spent
before the loop began (evidence, preflight, the baseline snapshot) count against the
time ceiling. A preparation that reaches the ceiling stops the loop `budget_exhausted`
with `stop_reason: budget_exhausted_during_round`. Each round is one look at the same
frozen bank, so `alpha` is split across `max_rounds` and the receipt states the number
of looks. With few independent roots the interval is wide: a bank of five scored roots
cannot show a lower bound near 1 at any `alpha`, so a bar must be declared against the
interval the bank can produce.

The round ledger is written to a private directory under the state home
(`loops/<run name>`), outside the project and the run directory. The candidate staged
for round `n` is `candidate-<call>` in the run directory, and `recommended_candidate`
points at the best one. Nothing is applied: `changed_files` stays empty, the project is
never written, and the loop produces no change plan, so adopting a candidate is a
separate step behind the existing gates.

An exception other than an interrupt or a budget stop is not a result: the run exits
with its existing typed status and reason (an invalid input is 2, an execution error 1)
and leaves a replayable ledger with no stop event, and a loop receipt is never written
for it. An AHL defect inside the loop (`loop_port_invalid`, `loop_ledger_invalid`,
`loop_ledger_full`, `loop_order_violation`, `loop_receipt_invalid`) is status
`requires_intervention`, exit 1, stage `run`.

### Later runs

A later loop starts from the harness that is on disk and evaluates its baseline fresh.
From the previous run it takes what [incremental runs](incremental-runs.md) already
detect (the harness files `applied`, `ignored`, `edited` or unchanged since then) and
adds the loop's own state, in the `carry_over` block of the receipt
(`ahl-loop-carryover-v1`):

| Key | Meaning |
| --- | --- |
| `status` | `carried`, `reopened` (the earlier dispositions no longer apply) or `unavailable` (the previous run was not a valid loop run, or could not be read) |
| `relation`, `comparable`, `reason` | The harness relation since the previous run, whether its frozen inputs equal this run's, and why dispositions reopened: `frozen_inputs_changed`, `harness_edited`, `harness_relation_unknown` or `mechanism_evidence_changed` |
| `historical` | The previous run's `before` and best scores, labelled `historical_observation`, with `comparison` `comparable` or `not_comparable` |
| `fresh`, `fresh_baseline_minus_historical_best` | This run's own scores, labelled `fresh_evaluation`, and their difference from the historical best, shown only for identical frozen inputs; otherwise `delta: omitted` with its reason |
| `mechanisms` | `carried` (the `rejected`, `inconclusive` and `deferred` mechanisms that are not offered again) and `reopened` (the ones a changed component or changed inputs released) |
| `history`, `truncated` | The last ten earlier runs, and which of `mechanisms` and `history` were pruned to their caps of fifty and ten |

Only the dispositions reach the controller. A previous score is never this run's
baseline evaluation, never a fresh trial and never an input to a decision: a baseline
that an earlier run accepted is evaluated again, and `usage.trials` counts only this
run's trials. A mechanism an earlier run kept (`accepted` or `improved`) is not carried,
since its candidate was the result. Dispositions are reopened, never silently kept, when
the frozen inputs differ or the harness was edited outside the earlier candidate. A
mechanism also reopens when it has a newly observed evidence reference. Removing a
reference alone does not reopen it. When evidence
cannot be matched against the previous receipt, including receipts created before
evidence fingerprints existed, `evidence_unknown` lists the affected mechanisms and
their prior dispositions remain in force until relevant new evidence is established.
Only uncapped, unambiguous brief entries with complete capture and a distinct reference
for each reported error get fingerprints. These digests
are private and do not establish that the source capture was complete. Unknown harness
relations reopen dispositions; unknown evidence does not by itself authorize a retry.
A pruned entry is forgotten and evaluated again; it never
becomes a success or an independence label.

### The receipt

The `loop` block of `workflow.json` and of the stdout receipt (`ahl-loop-receipt-v1`)
holds counts, digests, numbers and closed labels, never task, grader, candidate or
captured text.

| Key | Meaning |
| --- | --- |
| `spec` | The resolved declaration (`ahl-loop-spec-v1`): the eight fields, `limits`, `mode`, `sources` (the origin of each value) and its `digest`. |
| `frozen_inputs_sha256` | The bank, grader, seed schedule, per-arm budget share and runtime every evaluation ran on. |
| `mode`, `acceptance_source`, `looks`, `alpha_per_look` | The bar's mode and origin, the number of looks at the frozen bank and the error level of each. |
| `before` | The round-0 baseline: `snapshot`, `evaluation_id`, `validity` (`valid`, `vacuous`, `incomplete`), `evidence`, `vacuity`, `trials` and `score` (`point`, `lower`, `upper`). |
| `after` | The best snapshot (the baseline when no round improved it): the same fields plus `round`. |
| `rounds` | One entry per round: `round`, `mechanism`, `base`, `candidate`, `evaluation_id`, `validity`, `evidence`, `vacuity`, `trials`, `score`, `paired` (the delta against the best with its interval), `decision` (`accepted`, `improved`, `inconclusive`, `regressed`) and `disposition`. A comparison that ran on other inputs has no score, no paired delta and no decision. |
| `best_round` | The round of the best snapshot; 0 is the baseline. |
| `stop_criterion`, `stop_reason` | The one criterion that stopped the run, and why when the criterion has a reason: `reserve_for_evaluation_unavailable` or `budget_exhausted_during_round` for `budget_exhausted`, `unresolved_request` for an `interrupted` resume that might repeat an unresolved evaluation or proposal request. |
| `mechanisms`, `deferrals` | Each mechanism's disposition, and each deferred mechanism with its closed reason (`preparation_failed`, `invalid_proposal`, `no_change`, `candidate_identical_to_best`, `instruction_validation_*`, `instruction_removal_*` or the review reason). |
| `usage`, `events`, `ledger_head_sha256`, `budget_stages` | Trials and seconds spent, the number of ledger events and the SHA-256 of the last one, and the [stage budget](budget-stages.md). |
| `resumed` | Only for a run that was resumed: `count` and `abandoned_rounds`. An abandoned round has `abandoned: true`, `validity: incomplete`, no score and the disposition `abandoned`. In new journaled runs its `evaluation_id` is `null`; later completed evaluations use their actual artifact IDs. Legacy receipts retain their original IDs. |
| `carry_over` | Only for a later run: what the [previous loop run handed on](#later-runs). |
| `recommendation_only`, `automatic_promotion`, `certification` | Always `true`, `false` and `unverified`. |
| `remediation` | One content-free next step for the stop criterion. |

A score is reported only for an evaluation that scored at least one root: an
evaluation with no evidence, or one blocked because its inputs differed, carries no
number, so a placeholder never reads as a measured zero. Every evaluation carries an
`evaluation_id` (`evaluation-N`, the run directory it came from).

`validate_receipt()` runs on every loop result and fails closed with
`loop_receipt_invalid`, naming the first field that is not honest: a missing or unknown
`stop_criterion`; an authority field other than `recommendation_only: true`,
`automatic_promotion: false` and `certification: unverified`; no remediation step or
spec digest; a number with no evaluation id, an interval outside [0, 1] or out of order,
or a decision outside the closed set; more rounds than the cap of 10; an accepted stop
with no post-improvement evaluation (`acceptance_score_reached` needs a last round
decided `accepted`; `accepted_without_change` is the only accepted stop with no round);
an evaluation behind an accepted stop that is not valid, scored, labelled with no
vacuity and carrying a score; and a receipt over 64 KiB. A result that fails it is an AHL
defect (status `requires_intervention`, exit 1) and is never reported as a result.

### Stop criteria, statuses and exit codes

Every loop result names exactly one criterion, maps it onto a coarse status, records
the reason `loop_<criterion>` and carries that criterion's one remediation step. The
existing statuses and exit codes are unchanged.

| Stop criterion | Status | Exit | Remediation |
| --- | --- | --- | --- |
| `accepted_without_change` | `accepted` | 0 | The baseline meets the bar on the frozen development and regression evidence; nothing changed and this is not certification. |
| `acceptance_score_reached` | `accepted` | 0 | A candidate meets the bar on that evidence; nothing was applied and this is not certification. |
| `budget_exhausted` | `requires_intervention` | 1 | Raise `--timeout` or `limits.trials`, then rerun. |
| `interrupted` | `requires_intervention` | 1 | Resume the loop from its round ledger when safe. With `stop_reason: unresolved_request`, the built-in CLI cannot yet consume provider reconciliation and will not repeat the request. |
| `comparison_invalid` | `requires_intervention` | 1 | A frozen input differed between evaluations, an AHL defect: read the investigation, fix the cause, add a regression test, rerun. |
| `max_rounds` | `not_accepted` | 3 | The cap ended the loop with the bar unmet; raise `--max-rounds` or lower the bar. |
| `plateau` | `not_accepted` | 3 | Consecutive rounds produced no supported improvement; add evidence or `--focus`. |
| `regression` | `not_accepted` | 3 | A candidate made the frozen score worse; read the investigation and revise or discard the mechanism. |
| `no_actionable_mechanism` | `not_accepted` | 3 | No mechanism was left to try; add evidence or `--focus`. |
| `evidence_insufficient` | `not_accepted` | 3 | The evidence, vacuous or too thin, could not support a result; fix the named cause. |

Beside these, an invalid declaration is exit 2 (status `invalid_input`, the reason of
the field), `loop_requires_frozen_bank` is status `blocked`, exit 3, and an AHL defect
is `requires_intervention`, exit 1. `accepted` is not certification, and not accepted
does not show the harness is fine: no receipt, summary line or guidance text says the
harness is fine or improved without the evaluated evidence behind it, and
`certification: unverified` is always present. The stdout summary line on stderr
carries the status, the category, and the remediation step as `next=`.

### Receipt parity

`ahl improve` and `ahl skills invoke` write a loop block with the same keys. The only
values allowed to differ are the measured time (`loop.usage.seconds` and the seconds in
`loop.budget_stages`), the head of the round ledger (`loop.ledger_head_sha256`), whose events
are stamped with that time, and the run directory, listed in `agent_harness_lab.entry_parity`;
`tests/unit/workflow/test_entry_parity_durations.py` derives clock-based fields from
the loop's modules as it does for the rest of the receipt.

### Not in this release

A per-area `accept_score` map resolves but is not decided (it stops
`evidence_insufficient`), a first-use bank is not
constructed for the loop and an `--experiment-request` bank is not driven by it (both keep their labelled single pass), and a recommended
candidate has no change plan; each belongs to a later slice of the loop epic.

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

To test a resume, run a script into a ledger that interrupts after `after` events (the `interrupted_after_each_event` preset,
or `InterruptedLedger` with `ScriptedPorts.on_call` to interrupt at the next port call so no work is lost), then call
`controller.resume()` with fresh `ScriptedPorts(script, Meter(), evaluated=, proposed=, settled=)`: `evaluated` and `proposed`
say how many evaluations and proposals the ledger already holds, so the next ones are the script's next rounds, and `settled`
is what `settle()` answers (`None` for an unresolved request). Two Meters make the total charged the sum of both runs.

## Typed reasons

Each reason has one remediation in the [exit reason table](analysis.md#exit-reasons-and-next-actions). The errors name the reason, the field and the source and never repeat the value. A field or area name that comes from the project is named only when it is a plain identifier (letters, digits, underscore, hyphen and dot, up to 64 characters); any other key, including one with a control or escape character, is reported as `<unknown>`, so a hostile `ahl.json` cannot write to the terminal.

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
| `loop_port_invalid` | an evaluator, proposer, candidate, evaluation or clock outside its port contract; a bank that cannot be frozen as a scoring bank (`bank`) or whose frozen inputs cannot be read (`frozen_inputs`) |
| `loop_ledger_invalid` | a ledger root inside the project or on a link, an event out of order, a reused ledger, or a tampered or incomplete ledger read back |
| `loop_ledger_full` | a ledger event beyond `6 * max_rounds + 9`, or beyond that plus three for each of ten resumes in a reopened ledger |
| `resume_spec_changed` | a resume under a declaration other than the one the run was frozen with; nothing ran |
| `resume_inputs_changed` | a resume under other frozen inputs, baseline, mechanism queue or carried dispositions than the run was frozen with; nothing ran |
| `resume_busy` | a resume while a run or another resume holds the case; nothing ran |
| `loop_requires_frozen_bank` | a loop flag or declaration with no pinned task bank in `ahl.json`, or with a controlled bank: status `blocked`, never a silent single pass; the same reason labels a default run that made the single pass for that cause (`loop.status: not_used`) |
| `loop_receipt_invalid` | a loop receipt that failed `validate_receipt()`: a missing or unknown stop criterion, an accepted stop without its evaluation, a number without an evaluation id, an authority field other than recommendation only, or an oversized receipt |
