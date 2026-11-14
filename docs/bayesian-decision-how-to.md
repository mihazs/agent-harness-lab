# Add a Bayesian decision score to a plan and read it

Use this to get a numeric better, equivalent, worse or insufficient-evidence answer
beside the fixed Hoeffding analysis of a frozen plan, with no model call. The score is
recommendation-only: it never promotes anything, and the fixed analysis stays the only
path to `recommended`.

No peer-reviewed work applies a paired Bayesian posterior with a region of practical
equivalence to agent harness scoring. The score is an inference by analogy from nearby
literatures (see the [evidence status](bayesian-decision-score.md#evidence-status)). The
research report this design cites was not available, so every claim that depends on it is
unverified. For fields and rules see the [reference](bayesian-decision-reference.md); for
the reasoning see the [explanation](bayesian-decision-score.md).

## Before you start

- A fixed-final plan (no sequential design, no cohort pin) that freezes
  `precision_half_width`, as in [experiment precision](experiment-precision.md).
- At least as many independent roots as the larger of `min_roots` and 8. Fewer roots
  always give `insufficient_evidence`; repetitions of a root never count as roots.
- Your choices made before the first trial. Every parameter is part of the plan digest,
  so changing one afterwards means a new plan and new trials; never pick one after
  looking at a result.

## Declare the score in a plan

Add `bayes_decision` to the plan JSON next to the other frozen fields. The smallest
declaration:

```json
{"schema_version": 1, "profile": "bayesian-rope-decision-v1"}
```

Omitted keys take their defaults when the plan is constructed, and the filled values are
what gets frozen. For a plan with `effect_margin` .1 and `family_margin` .05 the frozen
form is:

```json
{
  "schema_version": 1,
  "profile": "bayesian-rope-decision-v1",
  "rope_epsilon": 0.1,
  "prior": {"kind": "dirichlet_pseudo_root", "concentration": 1, "anchor": 0},
  "p_cut": 0.99,
  "regression_margin": 0.05,
  "posterior_draws": 20000,
  "allow_bound_resolved_verdict": false,
  "cross_check_alpha": 0.01
}
```

`rope_epsilon` is the smallest difference in correctness probability that matters in
practice; the default reuses `effect_margin`. See the
[spec field reference](bayesian-decision-reference.md#spec-field) for every rule and the
[explanation](bayesian-decision-score.md#pre-registered-parameters) for why each default.
A plan without the declaration prints exactly what it printed before.

## Run it

Run the recorded trials of that plan through the usual command:

```sh
ahl experiment --spec plan.json --records trials.json
```

The exit status is 3: a recorded analysis of an unverified oracle is descriptive and
never exits 0. The JSON adds a `bayesian_decision` key beside `status` and `promotion`.
`ahl improve` and its decision receipt carry the same block for a sealed history of a
bank large enough to pass the unchanged precision preflight, and `ahl compare` displays a
supplied block as text (see [side-by-side reports](side-by-side-reports.md)); neither
recomputes it.

## Read the block

Read it in this order. An abridged block for the `clear_win` fixture below:

```json
{
  "verdict": "better",
  "reason": "all_gate_stages_passed",
  "stage": "resource",
  "gate": [
    {"stage": "validity", "status": "passed"},
    {"stage": "critical_veto", "status": "passed"},
    {"stage": "quality", "status": "passed"},
    {"stage": "regression_controls", "status": "passed"},
    {"stage": "resource", "status": "not_declared"}
  ],
  "roots": {"selected": 60, "independent": 60},
  "unknown_arms": 0,
  "score": {"estimate": 1.0, "S_worse": 0.0, "S_equiv": 0.0},
  "floors": {"roots": {"required": 8, "actual": 60, "met": true}},
  "cross_check": {"status": "evaluated", "method": "monte_carlo", "basis": "point", "alpha": 0.01, "agrees": true},
  "recommendation_authority": false,
  "automatic_promotion": false,
  "publication_allowed": false
}
```

1. `verdict`, `reason` and `stage` say what was decided and by which gate stage. The
   stages run in a fixed order (validity, critical veto, quality, regression controls,
   resource) and a later stage never undoes an earlier veto; `gate` shows each one.
2. `score` holds `S`, the probability that the candidate beats the baseline by more than
   `rope_epsilon` (`estimate`), and the probabilities of a loss and of practical
   equivalence. With any ungraded arm they are `null` and only the bounds `S_low` and
   `S_high` are reported.
3. `floors` shows the root floor and the precision floor. A floor that is not met
   withholds the verdict even when the posterior alone would pass.
4. `unknown_arms` and `bound_resolution` show how many outcomes are missing and whether
   collecting them could change the answer. An unknown outcome is never a failure.
5. `cross_check` is the exact or seeded sign-permutation test. It can only downgrade a
   verdict to `insufficient_evidence`, never raise one.
6. `families` and `resource_gate` show the per-family regression controls and the
   resource gate. A plan that pins a resource contract reports
   `resource_gate_not_evaluated` until a command computes that decision.

Every field, its meaning and the recorded input it traces to are in the
[output block reference](bayesian-decision-reference.md#output-block).

## Act on a verdict

| Verdict | What it means for you |
|---------|-----------------------|
| `better` | Evidence the candidate is better by more than `rope_epsilon`. A review aid: promotion still needs the fixed analysis and the existing policy. |
| `equivalent` | Evidence the difference is inside the region of practical equivalence. It is not a noninferiority certification. |
| `worse` | A critical regression, or evidence of a loss. Read `reason`; do not adopt the candidate. |
| `insufficient_evidence` | Nothing vetoed, but the evidence does not meet the floors or cuts. `reason` says which: add independent roots for `insufficient_independent_roots` or `insufficient_precision`, collect the missing outcomes for `unknown_outcomes`. |
| `blocked` | Validity failed (for example `provenance_mismatch` or `missing_or_duplicate_trials`); no score is meaningful. Fix the trial set. |

Re-running the same plan on the same roots is another look at the same evidence and is not
corrected for; choosing among several candidates inflates false recommendations.

## Reproduce the six examples

Six synthetic fixtures show every verdict end to end: a clear win, a clear loss, a tie, a
bank with unknown outcomes, a critical regression and a six-root starter bank. Their
verdicts are committed in
[`examples/bayes-decision/expected-verdicts.json`](../examples/bayes-decision/expected-verdicts.json)
and tabulated in the [reference](bayesian-decision-reference.md#fixtures). From a
repository checkout, with no network and no private data:

```sh
python3 scripts/build_bayes_fixtures.py all --out /tmp/ahl-bayes-fixtures
ahl experiment --spec /tmp/ahl-bayes-fixtures/clear_win/plan.json --records /tmp/ahl-bayes-fixtures/clear_win/trials.json
```

The first command writes `plan.json` and `trials.json` for each fixture into a new folder
named after it and refuses to overwrite an existing one. Replace `clear_win` in the second
command with `clear_loss`, `tie`, `mixed_unknowns`, `critical_veto` or `starter_bank`.
Everything is seeded: the same command prints the same block byte for byte, and a test
runs these exact commands for all six.

| Fixture | Expected `verdict` | Expected `reason` |
|---------|--------------------|-------------------|
| `clear_win` | `better` | `all_gate_stages_passed` |
| `clear_loss` | `worse` | `posterior_worse_cut_met` |
| `tie` | `equivalent` | `all_gate_stages_passed` |
| `mixed_unknowns` | `insufficient_evidence` | `unknown_outcomes` |
| `critical_veto` | `worse` | `critical_regression` |
| `starter_bank` | `insufficient_evidence` | `insufficient_independent_roots` |

`mixed_unknowns` is the case to study: `score.estimate` is `null` and `S_low` is below
`S_high`, because the ungraded arms could fall either way. `starter_bank` shows the root
floor: its posterior alone would clear `p_cut`, and six roots still give no verdict.

## Limits

- Few independent roots give wide, prior-dominated posteriors and an
  `insufficient_evidence` verdict. A six-root starter bank cannot reach a verdict.
- Repeated looks at one bank are not corrected, and the score is not a sequential test.
- Roots are assumed independent. Similar tasks that share a cause behave like one root;
  root-similarity evidence is heuristic.
- The score is calibrated only under declared synthetic models (see
  [Calibration](bayesian-decision-score.md#calibration-issue-e)), which bound its false
  `better` rate there and show it is no safer than the Hoeffding path when roots share an
  outcome. It is not observed improvement, certification or oracle validity.
- The score is recommendation-only: `automatic_promotion` and `publication_allowed` stay
  `false`, and no model call appears anywhere in it.

The [explanation](bayesian-decision-score.md#limits) gives each limit in full.
