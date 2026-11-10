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
activation remains unknown.
