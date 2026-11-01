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

## Explicit Codex 0.166.0 rollout profile

```sh
ahl analyze PROJECT --events PRIVATE_ROLLOUT.jsonl --format codex \
  --source-version codex-rollout-0.166.0
```

This opt-in metadata projection is pinned to public Codex tag `rust-v0.166.0`,
commit `6ff1e1ca3b30fc8b914720681fcf5fe62e95f856`: the
[rollout wire envelope](https://github.com/openai/codex/blob/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856/codex-rs/history/src/rollout_payload.rs),
[session/turn/usage metadata](https://github.com/openai/codex/blob/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856/codex-rs/protocol/src/protocol.rs),
[response items](https://github.com/openai/codex/blob/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856/codex-rs/protocol/src/models.rs)
and [physical ordinals](https://github.com/openai/codex/blob/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856/codex-rs/rollout/src/ordinal.rs).
It checks consumed metadata fields rather than deserializing every upstream content
field. Synthetic fixtures contain no real sessions. The version string describes
the capture's producer claim; the reader does not authenticate that producer.

The profile requires an explicit file, a matching `cli_version`, one session header
and exact declared session/turn cwd matching `PROJECT`. It never searches native
stores, loads parent histories, runs Codex or contacts services. Existing default
discovery and synthetic adapters keep their behavior. `--source-version` also
accepts existing declared adapter versions for explicit files; unsupported
versions/formats reject. The shared workflow passes the option through analysis,
improve and watch evidence collection without authorizing any new execution.

Thread, root-session, call and event identities are scoped fingerprints. Moving a
rollout into an archive does not create new event/case identities. Root-session
claims remain `lineage_verified: false`; they do not prove independent origin.
Response-item calls/results retain correlation; explicit command-end exit codes
are separate execution observations. String tool outputs never become inferred
exit status. Cancellation, compaction and turn stops remain ungraded. Unrecognized
items/events produce explicit coverage records. Unknown tool namespaces/names are
fingerprinted; bodies, arguments, results, reasoning, native paths and account
metadata are not copied to the ordinary ledger. The private source summary keeps
the input path as a fingerprint.

Turn-context model names become `requested_model_fingerprint`, not a served-model
identity, only on the context record that declares them. Explicit turn/root-turn
IDs become scoped fingerprints; delayed usage does not inherit a later turn's
model request. Only own-thread `token_usage_record.usage` increments are counted once
per response ID, with turn/root-turn identity checked for duplicate consistency.
Cumulative token-count snapshots are not summed. Inherited response usage is
excluded; missing served model, effective settings, injection and task outcomes
stay unknown.

Paginated captures require typed `HistoryPosition` metadata, a header ordinal
matching its physical base and ordered unsigned ordinals. Native subagent start
ordinals exclude inherited prefixes. Referenced base histories are not read, and
their completeness remains unknown. A logical fork cutoff alone does not locate
physical ownership: inherited legacy captures without an explicit physical
boundary/base reject. Parent-history resolution, broader cwd/worktree reconciliation,
compressed capture and bounded multi-file discovery remain open in #44/#58.

Safe POSIX reads reject links/special files, detect concurrent source changes and
cap one file at 64 MiB, each line at 1 MiB, records at 100,000 (or the lower requested
event limit), under a ten-second read/parse deadline. A final line without its
newline is omitted even when its JSON parses; the complete prefix and partial-tail
warning remain visible. Malformed complete records, duplicate keys, ordinal/usage
conflicts and unavailable safe filesystem APIs fail explicitly. Capture remains
partial, including after native completion. No source-journal cursor is advanced
by this explicit analysis path. `task test:captures:codex` runs the native projection,
privacy, ownership and CLI cases; wheel/archive tests exercise it after installation.
