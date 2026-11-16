# Score a harness per capability area

Use this when you want to answer "how good is this harness for UX/UI?" and "what is its
coding score?" separately, instead of one pooled number that can hide a weak area.
`ahl score` reads a recorded paired experiment and prints one row per declared area, each
with an evidence state. It calls no model, uses no network and writes no file, so you can
run it as often as you like. Every field is listed in the [score reference](score-reference.md);
the registry, states and receipt keys of all area surfaces are in the
[capability area reference](area-reference.md).

## Before you start

You need three things on disk:

- a project `ahl.json` that declares the areas and names the bank, and a bank whose tasks
  carry `areas` tags ([declare capability areas](declare-capability-areas.md));
- an experiment plan, the JSON `ExperimentSpec` that `ahl experiment --spec` takes, over
  tasks of that bank, with the same `root` and `family` the bank gives each task;
- the recorded trial rows for that plan, the JSON array that `ahl experiment --records`
  takes.

To follow along without a project of your own, use the [synthetic project](#try-it-on-a-synthetic-project)
below. To make the tasks of an area measurable, write [objective graders](author-area-graders.md) for them.

Mark each task `independence: "reviewed"`, `review_status: "accepted"` and
`permission: "approved"` in the bank only after a person has reviewed it. A task with any
other value stops its area from being scored: the area is `blocked`, because nobody has
shown that its roots and grader are independent.

## Print the report

```sh
ahl score . --spec experiment.json --records trials.json
ahl score . --spec experiment.json --records trials.json --format md
```

JSON is the default and is meant for tools. `--format md` prints a table for people.
Both list the areas in the order `ahl.json` declares them.

To look at one area, repeat `--area`:

```sh
ahl score . --spec experiment.json --records trials.json --area ux-ui
```

An id that `ahl.json` does not declare, or one listed twice, stops the command with exit
code 2 and a typed reason (`score_area_undeclared` or `area_duplicate`) that names the
position, such as `--area[1]`, and never the value.

## Read the state before the number

| State | What it means | What to do |
| --- | --- | --- |
| `scored` | Every outcome of the area is known, it has at least the plan's minimum of roots, and every task is reviewed as independent. | Read the paired status for the decision; the intervals are the fixed ones of the plan. |
| `provisional` | An interval is shown but no verdict: an outcome is unknown (`unknown_outcomes`) or there are too few roots (`below_minimum_roots`). | Record the missing trials or add independent roots, then run again. |
| `no_evidence` | No experiment task carries the area (`no_experiment_task`), or none of its outcomes is graded (`no_graded_outcome`). No number is printed, because it would be a zero. | Tag a task of the experiment with the area, or find out why nothing was graded. |
| `blocked` | The bank or a grader is not qualified (`bank_not_qualified`), or the experiment cannot be scored per area. No number is printed. | Fix the reason shown; the [reference](score-reference.md#reasons) lists each one. |

A task tagged with two areas counts in both and is never split or averaged between them.
An overall figure appears only when you ask for every declared area and every one is
`scored`; otherwise the report says why it is withheld.

## Try it on a synthetic project

From a checkout of the repository, build the synthetic two-area project. It is the starter
bank with its tasks tagged `coding` and `ux-ui`, plus a `docs` area that no task carries and
a `review` area whose one task nobody has reviewed. One `ux-ui` trial has no graded outcome.
Nothing in it is real: no model ran, and the outcomes are fixed by the generator.

```sh
python3 scripts/build_area_score_example.py --out /tmp/ahl-area-example
cd /tmp/ahl-area-example
```

Print the whole report for people:

```sh
ahl score . --spec experiment.json --records trials.json --format md
```

```text
# Per-area harness score: score-fixture

Recorded arms: baseline, candidate. Trials recorded 14 of 14. Interval alpha 0.05, minimum roots 3. Untagged experiment tasks: 0.

| Area | State | Reason | Tasks | Roots | Baseline success [interval] | Candidate success [interval] | Paired delta [interval] | Paired status | Critical regressions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Coding (`coding`) | scored |  | 4 | 3 | 0.000 [0.000, 1.000] | 1.000 [0.000, 1.000] | 1.000 [-0.839, 1.000] | inconclusive | 0 |
| UX/UI (`ux-ui`) | provisional | unknown_outcomes | 3 | 3 | 0.000 [0.000, 1.000] | 1.000 [0.000, 1.000] | n/a | incomplete | 0 |
| Docs (`docs`) | no_evidence | no_experiment_task | 0 | 0 | n/a | n/a | n/a | n/a | n/a |
| Review (`review`) | blocked | bank_not_qualified | 1 | 1 | n/a | n/a | n/a | n/a | n/a |

Overall: withheld (not_every_area_scored); it is shown only when every declared area is scored.

A task in several areas counts in each. No area is averaged into another, and intervals are not corrected across areas.
```

All four states are there. Read it the way the question asks:

- **How good is it for UX/UI?** The honest answer is "not yet known". The area is
  `provisional` because one outcome is unknown, so the interval is shown and no verdict is
  drawn. Record the missing trial, then run again.
- **What is its coding score?** The area is `scored`: every outcome is known and it has the
  plan's three roots. That is a statement about the evidence, not a good result. With three
  roots the interval of a success rate is `[0.000, 1.000]` and the paired delta
  `[-0.839, 1.000]` cannot exclude zero, so the paired status is `inconclusive`.
- `docs` has no task and `review` is not qualified, so neither prints a number. A zero would
  have claimed a measurement nobody made.

Ask for one area, and the report adds how many areas were examined:

```sh
ahl score . --spec experiment.json --records trials.json --area ux-ui --format md
```

```text
# Per-area harness score: score-fixture

Recorded arms: baseline, candidate. Trials recorded 14 of 14. Interval alpha 0.05, minimum roots 3. Untagged experiment tasks: 0.

| Area | State | Reason | Tasks | Roots | Baseline success [interval] | Candidate success [interval] | Paired delta [interval] | Paired status | Critical regressions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| UX/UI (`ux-ui`) | provisional | unknown_outcomes | 3 | 3 | 0.000 [0.000, 1.000] | 1.000 [0.000, 1.000] | n/a | incomplete | 0 |

Overall: withheld (area_selection); it is shown only when every declared area is scored.

A task in several areas counts in each. No area is averaged into another, and intervals are not corrected across areas.

Multiplicity: 4 areas were examined and no interval is corrected across them, so a result for one area among 4 is weaker evidence than the same result for one area chosen before the run.
```

An area the project does not declare stops the command with exit code 2:

```sh
ahl score . --spec experiment.json --records trials.json --area design
```

```text
ahl score: score_area_undeclared: stage=score_selection file=ahl.json field=--area[0] origin=project_declaration_defect. Next: Pass --area with an id declared in the ahl.json areas, or declare the area there with an id, a label and a description, then rerun. Field: --area[0].
```

## What a per-area score cannot tell you

- **Aesthetics are out of scope.** A score is the share of tasks whose executable grader
  said yes. Taste, brand feel and visual quality have no such grader, and `ahl score` does
  not grade with a model. An area measures what its [objective graders](author-area-graders.md)
  observe: markup, accessibility rules and pixels, not whether a design is good.
- **Few independent tasks give wide intervals.** The interval comes from the number of
  independent roots, not from the number of trials. For `n` equal roots at the default
  `alpha` of 0.05 its half-width is `sqrt(2 ln(2/alpha) / n)`. It takes about 120 roots to
  know a success rate to within 0.25 either way:

  | Independent roots | Half-width of the interval |
  | --- | --- |
  | 3 | 1.57 |
  | 10 | 0.86 |
  | 30 | 0.50 |
  | 100 | 0.27 |
  | 300 | 0.16 |

  More repetitions of the same task add trials, not roots. A task tagged with two areas helps
  both and is still one root.
- **Areas are not corrected against each other.** One area chosen before the run is a
  stronger claim than the best of several, which is why a narrowed report prints the number
  of areas examined.
- **A score is a recommendation-only view.** `scored` rests on the review declarations in the
  bank, which `ahl score` does not verify. It is neither certification nor adoption.
