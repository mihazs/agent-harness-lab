# Local telemetry contract (v2)

`ahl analyze/improve` write a private `events.v2.jsonl` ledger in the run output.
It contains bounded metadata, not message bodies, arguments, commands, tool
results, thinking, or credentials. The ledger is content-idempotent, append-only,
locked during ingestion, mode 0600, and refuses conflicting IDs or incomplete
writes. The core `Ledger` API accepts byte/record retention limits; reaching them
requires explicit rotation/deletion, never silent loss. Default limits are 64 MiB
and 100,000 records. Run directories use mode 0700. These are local private data;
hashes and opaque IDs do not authorize public export.

`telemetry.schema.event` upgrades v1 records to v2, validates bounded typed fields,
rejects unsupported schema versions and nonfinite/negative measurements, and drops
undeclared content fields. Supplied task/root/family, runtime/config/harness hashes,
state revisions, skill signals, timing and token measurements remain observations.
Missing values are not filled with invented measurements. Stop/completion and
successful process execution do not mean accepted task success.

`verified_outcome` accepts binary outcomes only from an executable grader with
valid status/ID/version, or a reviewed annotation. This is a provenance boundary,
not proof of grader correctness. Model hypotheses and native capture are unknown.
Legacy evaluation successes and failures are both retained as control candidates;
status-only logs remain unverified. An explicit valid grader envelope, consistent
process status, unchanged inputs and grader identity are required for a graded
outcome. Later certification must independently audit those graders.

| Adapter contract | Calls/results | Task outcomes | Actual skill activation |
| --- | --- | --- | --- |
| `codex-jsonl-v1` | Native call IDs; explicit structured exit errors | Unknown | Unknown |
| `claude-jsonl-v1` | Tool blocks and `tool_use_id`; `is_error` | Unknown | Unknown |
| Normalized v1/v2 | Supplied metadata | Provenance-dependent | Supplied signal only |

These names identify the tested JSONL contracts, not verified CLI release versions.
Synthetic tests cover interleaving, duplicate/unmatched calls/results, cancellation,
explicit lineage, malformed messages and secret canaries. Capture completeness and
independence are unverified unless separately established. Future native shapes
require a new declared adapter version. The source-format check fails explicitly
for unsupported versions. Missing tool results produce coverage events; tool errors
remain observations rather than task-failure labels.

Discovery has separate limits of 2000 scanned capture files and 100 accepted
project captures. Within the bounded scan, files are considered newest first and
filtered by exact project before the accepted allowance is consumed. Exhausting
either limit is reported. This is still a bounded view, not complete history.
The existing 64 MiB input-read and event limits remain enforced.

No real-model effectiveness experiment was run for this schema. Tests establish
metadata/control-flow behavior only. No model weights, downloads, external ledger
service or ML dependency is required.
