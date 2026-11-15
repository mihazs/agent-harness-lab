# Repair a supported skill header

The explicit repair mode removes a UTF-8 BOM before an otherwise valid minimal
`SKILL.md` header in a project-owned skill bundle. See the [repair contract](one-call-improvement.md#independently-verified-format-repair)
for eligibility, authorization, transaction behavior and limits.

Run in the target project:

```sh
ahl improve . --repair-contracts
```

A `recommendation_only` project configuration stages the plan and leaves target
files unchanged. Otherwise the supported repair uses a private journal. The result
includes the deployment ID and journal location; keep plans and receipts private.

To restore an applied repair, use the deployment ID from that result:

```sh
ahl rollback DEPLOYMENT_ID --project /path/to/project
```

Rollback reports a conflict rather than overwriting intervening edits. Runtime
activation remains unknown. To repair your user-level skills instead of, or together
with, the project's, see [choose the project or user-level scope](improve-scope.md).

## Review a saved plan, or apply it again after a rollback

Every repair run keeps its change plan, baseline and candidate in the run directory
that `ahl improve` printed. To see exactly what that plan would change, and whether
it still matches the project, run:

```sh
ahl apply RUN_DIRECTORY --project /path/to/project
```

This prints a review packet and writes nothing. When the packet says
`awaiting_review`, copy its `plan_sha256` to authorize that exact plan:

```sh
ahl apply RUN_DIRECTORY --project /path/to/project --authorize-plan PLAN_SHA256
```

Use this after `ahl rollback`, or after a repair run stopped before writing (for
example when its time budget ran out). A project `ahl.json` keeps the plan
recommendation-only: the packet is `blocked`, or `stale` if the file was added or
removed after the plan. A `stale` packet means the project, configuration, baseline,
candidate or policy changed since the plan; rerun `ahl improve . --repair-contracts`
to make a new plan. If you press Ctrl-C during an authorized `ahl apply`, repeat the same command: it applies once, or
reports `stale` when the reviewed bytes are already installed, in which case finish the deployment the message names with
`ahl promotion PROJECT --deployment ID --action recover`. See the [packet and binding contract](one-call-improvement.md#review-and-apply-a-saved-plan).
