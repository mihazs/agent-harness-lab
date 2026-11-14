# Declare capability areas and tag bank tasks

Use this when you want a harness result per capability area, such as coding or UX/UI,
instead of one pooled number. This step only declares areas and tags tasks; it does not
score anything yet, so no model is called and nothing is executed.

An area is separate from a task's `family`. The family stays the statistical grouping of
related tasks; an area says which capability the task exercises.

## Declare the areas

Add `areas` to the project's `ahl.json`. Each entry has exactly an `id` (lowercase
kebab-case), a `label` and a `description`. Nothing is declared for you: copy the
suggested entries below that fit, or write your own. Any well-formed id is allowed.

```json
"areas": [
  {"id": "coding", "label": "Coding", "description": "Locate, change and verify behavior in source code."},
  {"id": "ux-ui", "label": "UX/UI", "description": "Build and review user-interface behavior, layout and flows."}
]
```

Suggested starting points: `coding`, `ux-ui`, `docs` and `review`
(`agent_harness_lab.evaluation.areas.SUGGESTED` holds their wording).

## Tag the tasks

In the bank's `bank.json`, give a task an `areas` list of one to three declared ids.
A task in two areas counts in both and is shown that way; areas are never pooled behind
your back. A task without `areas` stays untagged and counts in no area.

```json
{"task_id": "layout-regression", "areas": ["coding", "ux-ui"], "...": "the usual task fields"}
```

Changing `bank.json` changes its digest: set `bank.sha256` in `ahl.json` to the new value.

## Check the declarations

`ahl bank --manifest PATH` lists the task count per area (`areas`) and checks that every
tag is well formed. It cannot know your registry, so an undeclared id is caught where the
project is known: `ahl improve` stops before dispatching anything, and a configured
evaluation stops before any trial, with a reason such as `task_area_undeclared` and the
field to fix (for example `tasks[2].areas[1]`). The reasons and limits are listed in the
[evaluation manifest contract](evaluation-manifest-contract.md#capability-area-declarations).
