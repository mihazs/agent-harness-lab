# Why the loop evaluates, improves, then evaluates

`ahl improve` used to prepare a candidate first and score the baseline and the candidate
together afterwards. That order cannot say whether the harness needed improving, so a
baseline score could never gate an improvement, and a run that spent its budget on a
harness that already met the bar looked the same as one that fixed a real failure. The
loop is the default now because the order below is the only one under which a result can
be read as a before and an after. This page explains the choices; the
[how-to](improve-until-score.md) runs the loop and the [reference](loop-reference.md)
lists every field, reason and exit code.

## Why the order is fixed

1. **Evaluate the baseline first.** A baseline that is valid, scored and meets the bar stops
   the run as `accepted_without_change` with no improvement spend.
2. **Improve only from a valid evaluation of the current best.** One mechanism per round, so
   a result can be attributed to the change that made it. A round starts only when what is
   left of the budget also pays for re-evaluating its candidate; otherwise the run stops
   `budget_exhausted` before improving.
3. **Evaluate again on the same frozen inputs.** The bank, graders, seed schedule, per-arm
   budget share and runtime are hashed once and observed around every evaluation. A
   different digest ends the run `comparison_invalid`, which names an AHL defect and never
   a verdict on the harness, so a changed grader can never make a candidate look better.

A state machine enforces the order and a receipt validator fails closed with
`loop_order_violation` when a result lacks the evaluation behind it. A vacuous evaluation
(no eligible task, every outcome unknown, identical arms, a grader that did not run, zero
trials) can neither stop as accepted nor justify an improvement.

## Why scores carry intervals

A success rate over a handful of independent roots is not precise. Every score is reported
with an interval, and the bar is met only when the interval's lower bound reaches it, so a
bank of five scored roots cannot show a lower bound near 1 whatever the harness does. Then
the honest result is `evidence_insufficient`, not a point-value pass. An outcome that is not
passed or failed leaves its root unscored: unknown capture or lineage never becomes a
success, a failure or an independence label.

## Why each round is a look and alpha is split

Every round examines the same frozen bank, and the chance of a false positive grows with
the number of looks. The declared `alpha` is therefore divided by `max_rounds`, fixed in the
plan before round 0, and the receipt states the number of looks and the error level of each.
Raising `--max-rounds` makes each look stricter, which is why a low cap and a bar the bank
can show beat a high cap and an unreachable bar.

## What accepted does not mean

`accepted` means a candidate meets the declared bar on the frozen development and regression
evidence. It is not certification. Held-out and certification inputs are never read to choose
a candidate, the loop produces no change plan, nothing is applied, and the receipt always
states `recommendation_only: true`, `automatic_promotion: false` and
`certification: unverified`. Adoption stays behind the existing gate. Not accepted does not
mean the harness is fine either: `max_rounds`, `plateau` and `no_actionable_mechanism` say
what was tried, not what is true.

## Limits with nondeterministic runtimes

Model runs differ from call to call. The loop's "same inputs" means the same frozen digests
and seed schedule, not identical outputs, so a repeated evaluation of one snapshot can score
differently and a regression inside the noise margin is not a finding. A second run of the
same command starts from the harness on disk, evaluates its baseline fresh, and never reuses
an earlier trial or score; it carries over only which mechanisms were rejected or
inconclusive, and reopens them when the frozen inputs or the harness changed.

## Where the default stops

The loop needs one frozen bank and a cooperative runtime. A project without a pinned bank, a
controlled (Responses) runtime and a run with `--experiment-request`, which needs the starter bank, keep their single pass,
and the receipt labels it `loop.status: not_used` with the reason and one next step, so no
run is a silent single pass. Constructing a frozen bank for the loop on first use and
driving an experiment request with it are not part of this release. `--single-pass` keeps the
earlier pass for one release so that an existing automation has time to move.
