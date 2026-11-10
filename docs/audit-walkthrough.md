# Try the offline audit on a synthetic harness

This walkthrough uses the bundled `examples/flawed-audit` fixture. Its instruction
file is intentionally empty, and its hook configuration uses an invented event.
It contains no real project history or credentials. From the repository root,
with Python 3.11 or later, run:

```sh
python -m agent_harness_lab audit examples/flawed-audit --runtime claude-code --json
```

The command reads the fixture without running its hook or calling a model or
network service. It prints JSON to stdout and writes a private JSON copy under
`$XDG_STATE_HOME/agent-harness-lab/audits` (or the default local state directory);
the report path appears on stderr. It does not change the fixture.

In stdout, `status` is `completed`. The `findings` array includes an `enforcement` row
with reason `hook_unknown_event` and an `instructions` row with reason
`instruction_empty`. Each row carries a `source`, `evidence_kind`, `cost_class`,
`remediation`, and the notice `convention_check_not_performance_evidence`.
Cheaper deterministic edits appear before content edits. The `coverage` array
also names areas that remain `not_checked`, and `next_action` suggests how to
seek behavioral evidence. The exact IDs and private report path are not needed
for this walkthrough.

These findings are convention checks. They are not evidence that this harness
behaves worse and they are not a performance promise. The audit does not apply
changes, measure an improvement, or establish that a proposed edit will help.
See the [static audit contract](static-audit.md) for the supported checks and limits.
