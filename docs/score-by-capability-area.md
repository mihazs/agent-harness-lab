# Score a harness per capability area

Use this when you want to know how a harness does for one kind of work, such as coding or
UX/UI, instead of one pooled number that can hide a weak area. `ahl score` reads a
recorded paired experiment and prints one row per declared area, each with an evidence
state. It calls no model, uses no network and writes no file, so you can run it as often
as you like. Every field is listed in the [score reference](score-reference.md).

## Before you start

You need three things on disk:

- a project `ahl.json` that declares the areas and names the bank, and a bank whose tasks
  carry `areas` tags ([declare capability areas](declare-capability-areas.md));
- an experiment plan, the JSON `ExperimentSpec` that `ahl experiment --spec` takes, over
  tasks of that bank, with the same `root` and `family` the bank gives each task;
- the recorded trial rows for that plan, the JSON array that `ahl experiment --records`
  takes.

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

## See where recorded sessions look like they happen

Controlled scores say how well the harness does in an area; they do not say where your
real work happens. Add `--sessions` to place recorded sessions beside the scores:

```sh
ahl score . --spec experiment.json --records trials.json --sessions sessions.json
```

`sessions.json` is a list of content-free summaries, one per session: an opaque id and counts
of tool names, file extensions and command families, never paths or text. The
[reference](score-reference.md#session-summaries) lists the exact shape.

The report then adds an advisory section. Read it as context only:

- It is never `scored`. A session is not a controlled task, so the section cannot raise an
  area's state, and an area can hold many sessions while its row above is `no_evidence`.
  That gap is the useful signal: real work happens in an area that the bank does not measure.
- A session that has no recognized signal, too few, a mixed signal (`ambiguous`) or looks like
  an area `ahl.json` does not declare stays `unclassified` with the reason; it is not moved to
  the nearest area. See the [rules and reasons](score-reference.md#rules).
- `--area` narrows the rows shown, not the placement; `not_selected` counts the sessions it hides.

