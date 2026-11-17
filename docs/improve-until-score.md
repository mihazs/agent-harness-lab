# Improve until an acceptance score is reached

Use this when you want `ahl improve` to keep improving a harness until it meets a score
you declare, or to stop with a typed reason that says why it could not. The loop is what
`ahl improve` does by default whenever the project pins a task bank: it evaluates the
baseline, improves one mechanism, evaluates the candidate on the same frozen inputs and
repeats. Why that order is fixed and what its numbers can show is in
[why the loop evaluates, improves, then evaluates](loop-rationale.md); every field and
reason is in the [loop reference](loop-reference.md).

Every command below runs in this repository's test suite against a synthetic project, a
pinned copy of the starter bank and a stub agent, and each stops with the criterion written
beside it (`tests/integration/docs/test_improve_until_score_howto.py`). Nothing here uses a
real project, session or model.

## Before you start

The examples use a synthetic project that pins a five-root bank and declares
`"loop": {"alpha": 0.999}` in `ahl.json`: five scored roots cannot clear a bar at the default
`alpha` of 0.05, so a project with a bank this small declares a high one (and a real bank
should be larger, not the declaration looser).

- `ahl.json` pins a task bank (`bank.path` other than `starter`, with its `bank.sha256`)
  and names a cooperative runtime. Without a pinned bank there is nothing to freeze: a plain
  `ahl improve` makes the previous single pass and says so in its receipt as `loop.status:
  not_used` with the reason `loop_requires_frozen_bank`, and a run that declares a score
  without one is `blocked` with that reason.
- The loop never applies anything. Every result is a recommendation, and `accepted` is not
  certification (see [what accepted means](loop-rationale.md#what-accepted-does-not-mean)).

## Run the loop with the defaults

```sh
ahl improve .
# stops: acceptance_score_reached (exit 0)
```

With no score declared the loop stops when its analysis reports a supported improvement
over its own round-0 baseline, and it labels that bar so nobody reads it as an absolute one:
`loop.acceptance_source` is `default` and `loop.mode` is `relative_supported_improvement`.
It never invents an absolute score.

## Declare the score and the round cap

```sh
ahl improve . --accept-score 0.1 --max-rounds 3
# stops: acceptance_score_reached (exit 0)
```

`--accept-score X` is the bar: the lower bound of the success rate on the frozen
development and regression tasks must reach `X`, a number above 0 and at most 1. Each
round is one look at the same bank, so `alpha` is split across `--max-rounds` (at most 10,
default 3) and the receipt states the number of looks. The same fields can live in a `loop`
object of the project `ahl.json` or of the user file; a flag overrides the user file, which
overrides the project file, which overrides the default, and the receipt names the source
of every value. A small bank gives a wide interval, so declare a bar the bank can show:
five scored roots cannot show a lower bound near 1.

## Read the stop criterion

The loop ends with exactly one `loop.stop_criterion` in `workflow.json` and on stdout, and
`loop.remediation` holds its one next step. The exit code maps the criterion:

| Exit | Status | Stop criteria | What to do |
| --- | --- | --- | --- |
| 0 | `accepted` | `accepted_without_change`, `acceptance_score_reached` | Review `loop.rounds` and the recommended candidate, then certify independently before adopting it. |
| 1 | `requires_intervention` | `budget_exhausted`, `interrupted`, `comparison_invalid` | Raise `--timeout`, rerun, or report the AHL defect `comparison_invalid` names. |
| 3 | `not_accepted` | `max_rounds`, `plateau`, `regression`, `no_actionable_mechanism`, `evidence_insufficient` | Read `loop.remediation`: it does not show the harness is fine. |
| 2 | `invalid_input` | none | Fix the declaration the reason names; nothing ran. |

## Stop at the cap when the bar is out of reach

```sh
ahl improve . --accept-score 0.9 --max-rounds 1
# stops: max_rounds (exit 3)
```

`--max-rounds 1` is exactly one evaluate, improve, evaluate pass. When it ends below the
bar, the criterion is `max_rounds` and the receipt keeps the candidate as evidence, not as a
result.

## Stop without spending when the harness already meets the bar

```sh
ahl improve . --accept-score 0.1
# stops: accepted_without_change (exit 0)
```

A baseline that meets the bar on valid, scored evidence stops before any improvement call.
That run changed nothing and spent no preparation.

## Rerun or resume

A later `ahl improve .` starts from the harness on disk, evaluates its baseline fresh and
carries over the mechanisms an earlier run rejected or found inconclusive (the `carry_over`
block); an earlier score is never this run's baseline. `ahl improve . --resume RUN_ID`
returns the verified receipt of a finished loop run without a model call. For a new
interrupted loop with a frozen recovery context, it validates the ledger and inputs before
continuing. It never repeats an unresolved request. The built-in CLI evaluator cannot yet
consume a settlement result, so those runs remain interrupted even after external
reconciliation ([resuming a loop](loop-reference.md#resuming-a-loop)).

```sh
ahl improve . --resume RUN_ID
# resumes a verified loop or returns its finished receipt
```

`RUN_ID` is the final directory name of the run's private output, printed on stderr as
`Private run artifacts`. A resume takes no other option.

## Set a bar per area

Declare areas in `ahl.json` ([capability areas](declare-capability-areas.md)) and give
`accept_score` a map of those ids to scores in the `loop` object. A per-area map resolves
and is checked, but it is not decided yet: such a run stops `evidence_insufficient` rather
than claiming an area result. Give one number until per-area decisions ship.

## Keep the previous single pass for one release

```sh
ahl improve . --single-pass
# loop: not_used (exit 3)
```

`--single-pass` makes the earlier improve-first pass: one preparation, then the baseline and
the candidate scored together, with the held-out task. It is deprecated and removed after
one release; the receipt says `loop.status: not_used`, `loop.reason: single_pass_requested`
and `loop.deprecated: true`. It does not combine with `--accept-score`, `--max-rounds`,
`--plateau-rounds` or `--on-regression`. `ahl watch` keeps its single pass.
