# Local telemetry contract (v2)

`ahl analyze/improve` write a private `events.v2.jsonl` ledger in the run output.
It contains bounded metadata, not message bodies, arguments, commands, tool
results, thinking, or credentials. The ledger is content-idempotent, append-only,
locked during ingestion, mode 0600, and refuses conflicting IDs or incomplete
writes. The core `Ledger` API accepts byte/record retention limits; reaching them
requires explicit rotation/deletion, never silent loss. There is no default byte
or record ceiling; callers may supply positive retention limits. Run directories use mode 0700. These are local private data;
hashes and opaque IDs do not authorize public export.

`telemetry.schema.event` upgrades v1 records to v2, validates nonempty typed fields,
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
| `claude-jsonl-2.1.316` | Explicit native file, writer/UUID scope, parallel tool blocks, compaction and unambiguous response usage | Unknown | Unknown; declared `Skill` names and exact project `SKILL.md` read requests only (`observed_declared`) |
| Normalized v1/v2 | Supplied metadata | Provenance-dependent | Supplied signal only |

These names identify the tested JSONL contracts, not verified CLI release versions.
Synthetic tests cover interleaving, duplicate/unmatched calls/results, cancellation,
explicit lineage, malformed messages and secret canaries. Capture completeness and
independence are unverified unless separately established. Future native shapes
require a new declared adapter version. The source-format check fails explicitly
for unsupported versions. Missing tool results produce coverage events; tool errors
remain observations rather than task-failure labels.

Discovery, capture records, evaluation reports and event analysis have no default
size ceiling. Optional positive `--max-capture-scan`, `--max-captures` and
`--max-events` values bound only the selected run. Files are considered newest
first and filtered by exact project before an explicit accepted-file allowance
is consumed. Malformed records retain explicit caveats; valid large records and
reports are read in full and their input hashes cover all consumed bytes.

Observation matching retains all eligible history by default. Explicit API cache
and pending-call correlation limits remain available. Recurring-sequence analysis indexes all contiguous
lengths and retains every supporting reference; `--sequence-length` optionally
limits lengths. Repetition still requires three reported roots and does not
establish independence. The index avoids constructing patterns that do not recur
across three roots. The number of recurring patterns can be quadratic; spelling
out every pattern's members can require cubic output in the worst case.
Available memory, storage and time remain practical constraints, not hidden caps.

No real-model effectiveness experiment was run for this schema. Tests establish
metadata/control-flow behavior only. No model weights, downloads, external ledger
service or ML dependency is required.

## Worked example: a synthetic Claude tool error

[`examples/claude-tool-error.jsonl`](../examples/claude-tool-error.jsonl) is four
invented rows in the `claude-jsonl-v1` contract from the table above: a `Bash`
call that returns `is_error: true`, an assistant line claiming the task failed,
and a `Read` call whose result never arrives. The session, IDs, timestamps, path
and the `SYNTHETIC-CANARY-BODY` marker were written for this page; no real session
was recorded or edited to produce them. From the repository root, pipe this
snippet to `python3 -`. It runs offline with the standard library:

```python
import json
from pathlib import Path
from agent_harness_lab.telemetry.schema import serialize
from tools.diagnose import analyze, normalize

lines = Path('examples/claude-tool-error.jsonl').read_text().splitlines()
rows = [json.loads(line) for line in lines]
records = list(normalize(rows, 'claude', source_version='claude-jsonl-v1'))
for record in records:
    print(serialize(record))
summary = analyze(records)
print(json.dumps(summary['field_coverage'], sort_keys=True))
print(json.dumps(summary['findings']))
```

It prints:

```text
{"activity":"tool_call","actor":"agent","call_id":"toolu_synthetic_1","capture_order":0,"capture_status":"unverified","case_id":"synthetic-walkthrough","event_id":"synthetic-a1:1","missing_reason":"no_independent_outcome","outcome":"unknown","outcome_provenance":"unknown","schema_version":2,"source_format":"claude","source_version":"claude-jsonl-v1","timestamp":"2026-01-01T00:00:00Z","tool_name":"Bash"}
{"activity":"tool_result","actor":"tool","call_id":"toolu_synthetic_1","capture_order":1,"capture_status":"linked","case_id":"synthetic-walkthrough","event_id":"synthetic-u1:0","execution_status":"error","missing_reason":"no_independent_outcome","outcome":"unknown","outcome_provenance":"unknown","schema_version":2,"source_format":"claude","source_version":"claude-jsonl-v1","timestamp":"2026-01-01T00:00:01Z","tool_name":"Bash"}
{"activity":"assistant","actor":"unknown","capture_order":2,"capture_status":"unverified","case_id":"synthetic-walkthrough","event_id":"synthetic-a2","missing_reason":"no_independent_outcome","outcome":"unknown","outcome_provenance":"unknown","schema_version":2,"source_format":"claude","source_version":"claude-jsonl-v1"}
{"activity":"tool_call","actor":"agent","call_id":"toolu_synthetic_2","capture_order":3,"capture_status":"unverified","case_id":"synthetic-walkthrough","event_id":"synthetic-a3:0","missing_reason":"no_independent_outcome","outcome":"unknown","outcome_provenance":"unknown","schema_version":2,"source_format":"claude","source_version":"claude-jsonl-v1","timestamp":"2026-01-01T00:00:03Z","tool_name":"Read"}
{"activity":"capture_coverage","call_id":"toolu_synthetic_2","capture_status":"unmatched_call","case_id":"synthetic-walkthrough","event_id":"coverage:unmatched:0","missing_reason":"capture_ended_without_result","outcome":"unknown","outcome_provenance":"unknown","schema_version":2}
{"lineage_verified": {"known": 0, "total": 5}, "outcome": {"known": 0, "total": 5}, "state_revision": {"known": 0, "total": 5}, "tool_name": {"known": 3, "total": 5}}
[]
```

The first five lines are the exact records `Ledger.ingest` appends to
`events.v2.jsonl` for these rows; `tools/tests/test_telemetry.py` runs the snippet
and the ledger against this page, so the output cannot drift unnoticed.

- **Bodies are omitted.** The command, the error text, the assistant sentence, the
  `Read` path and every copy of the canary are absent. In a line with tool blocks,
  only those blocks become events, so the text block before the `Bash` call leaves only its position in
  `event_id` (`synthetic-a1:1`), and the text-only line becomes a bare
  `assistant` event.
- **A tool error is not a task failure.** `is_error: true` sets
  `execution_status: error` on the linked result, and nothing else. Every record,
  including the assistant line that says the task failed, keeps `outcome: unknown`
  with `outcome_provenance: unknown`. The four event records carry
  `missing_reason: no_independent_outcome`, and the coverage record carries
  `missing_reason: capture_ended_without_result`.
  Only an executable grader or a reviewed annotation can supply an outcome; see
  `verified_outcome` above. The failed command may have been an expected probe.
- **A missing result is coverage, not an event.** The `Read` call gets a
  `capture_coverage` record with `capture_status: unmatched_call` and
  `missing_reason: capture_ended_without_result`. No result, error or
  cancellation is invented for it.
- **State and fingerprints are missing.** This adapter emits no
  `state_revision`, `tool_effect`, `mutation`, `input_fingerprint` or
  `result_fingerprint`, and does not derive them from the omitted bodies. Diagnosis
  therefore counts `state_revision` as known for 0 of 5 events and can report no
  finding; an empty `findings` list means nothing was comparable, not that the
  session was healthy.

Normalization is not general sanitization. It drops undeclared fields, but the
declared ones are copied verbatim: the native `sessionId`, `uuid`-derived event
IDs, `tool_use` IDs, timestamps, tool names and, for a row without tool blocks,
the native row `type` (as `activity`) appear as written. A real capture's
ledger stays private local data. The `claude-jsonl-v1` name identifies the
synthetic contract tested here; compatibility with any released Claude Code CLI
is unverified. The [pinned 2.1.316 profile](#explicit-claude-code-21316-file-profile)
below is the version-pinned native contract, and its synthetic fixtures do not
verify a live CLI either.

## Explicit Claude Code 2.1.316 file profile

```sh
ahl analyze PROJECT --events PRIVATE_SESSION.jsonl --format claude \
  --source-version claude-jsonl-2.1.316
```

This opt-in profile consumes the native entry/block family described by Anthropic's
[SDK 0.2.176 native reader](https://github.com/anthropics/claude-agent-sdk-python/blob/2146f9d8985513053e904b13200c8f744b36785a/src/claude_agent_sdk/_internal/sessions.py)
and [parallel-tool fixtures](https://github.com/anthropics/claude-agent-sdk-python/blob/2146f9d8985513053e904b13200c8f744b36785a/tests/test_sessions.py),
pinned at commit `2146f9d8985513053e904b13200c8f744b36785a`.
That [SDK release](https://github.com/anthropics/claude-agent-sdk-python/releases/tag/v0.2.176)
bundles CLI 2.1.316. The SDK is a wire-reference only: AHL neither imports it nor
runs Claude. Native JSONL is internal; this profile validates consumed fields and
supports one explicitly selected, consistently attributed writer per file.
Synthetic fixtures validate the reader; no live CLI session was used.

Attributable user/assistant/system/progress/attachment records require explicit
`version: 2.1.316`, exact declared `cwd` matching `PROJECT`, `sessionId`, `uuid`,
timezone timestamp and boolean `isSidechain`. Session/writer changes within a file
reject. A sidechain also requires `agentId`; optional `teamName` participates in
writer scoping. Metadata without a supported envelope contributes visible coverage
gaps, not fabricated events. Incompatible provided versions or project labels
reject. No directory name, timestamp, prompt text or filename establishes identity.
The existing `claude-jsonl-v1` synthetic adapter remains supported. `ahl analyze
--source-root` selects a bounded Claude `projects` directory with this pinned
version (see [bounded Claude project-root selection](#bounded-claude-project-root-selection)),
through the reader of the `2.1.316-v2` source-journal profile in
[source contracts](source-contracts.md#explicit-bounded-claude-project-root-journal).
Claude never falls back to a home scan when this explicit version is selected.

Replay approved observations in physical file order, including branches, parallel
tool results and pre-compaction history; do not reconstruct a preferred resume leaf.
Each event keeps its line's `capture_order` and adds the declared
`claude-native-order-v1` subordinal: its content-array position, with response
usage/stop facets after the line's blocks, plus the file segment. This is
serialization order within one line, not execution order, and it never orders
files; see [declared source order](analysis.md#declared-source-order).
Tool-use/result IDs, `is_error`, text/reasoning block kinds, native compaction flags
and reported response-stop markers map to metadata. Arguments, output, thinking,
message text, paths and credentials are omitted. Known built-in tool names remain
labels; other names are fingerprinted. Text such as "ERROR" does not supply a
missing structured error flag. A `Skill` call does not prove skill activation;
see [declared skill signals](#declared-skill-signals).
`isMeta` and `isCompactSummary` records cannot replay embedded tool blocks as new
executions. Unsupported blocks/entry types and missing call IDs remain explicit.
No textual cancellation or task-success label is inferred.

### Declared skill signals

The pinned profile's capability is `skill_activation: False` with
`skill_signal: observed_declared` (`claude_native.CAPABILITY`). Two tool-call
shapes add `skill_signal`, `skill_id` and `skill_signal_confidence` to their
`tool_call` event; nothing else changes:

| Native call | `skill_signal` | `skill_signal_confidence` |
| --- | --- | --- |
| `Skill` with a nonempty string `input.skill` | `declared_invocation` | `declared_name` |
| `Read` whose `input.file_path` is exactly `PROJECT/<prefix>/<name>/SKILL.md` | `file_read` | `full_read_request`, or `partial_read_request` when `offset`, `limit` or `pages` is present |

`<prefix>` is `.agents/skills`, `.claude/skills` or `skills`, the project-owned
bundle layout of [workflow adoption](workflows.md), and `<name>` is one path
component. The path must be absolute, already in canonical form and under the
selected project; equivalent spellings, parent steps, nested or other-root
`SKILL.md` files, relative paths and global skill directories yield no signal. No
filesystem is consulted, so a match records the layout, not that a bundle existed.
`skill_id` is `digest(('claude-skill-name-v1', name))` for the declared name or
the bundle directory name, so a declared name and its project directory can share
a label; it is a pseudonym in the private ledger, not anonymization. Skill
arguments, paths, file content and results are never copied, and `skill_hash`
stays absent because no content is read.

**Assumption.** The pinned SDK reference establishes the `Skill` and `Read` tool
names and the `tool_use` envelope, not their input fields. The `skill` and
`file_path`/`offset`/`limit`/`pages` names follow Claude Code's public tool
interface and are not verified against a live 2.1.316 session. A `Skill` call
without a usable name, a non-object input or a missing path stays an ordinary
call with no signal.

**Not established.** Both signals are model requests. The result may fail, a read
may be partial, a file may be read to edit it rather than to follow it, and the
runtime may load skill text implicitly without any call. Effective scope,
namespace resolution and shadowing between project and global copies are unknown,
and Bash or other tools that read a skill file are not mapped. These values are not
`read`/`invocation` evidence under `evaluation.skills.activation`, so they never
set `activated`, an outcome or utility. Row- or block-level labels supplied in a
capture are ignored. Captures without these calls keep their pre-change events,
identities, collection fingerprints and summaries; summaries gain no keys. The
`codex-jsonl-v1`, `claude-jsonl-v1` and pinned Codex rollout adapters are
unchanged.

`native_message_fingerprint` identifies the containing native UUID, while
`native_parent_message_fingerprint` records its optional parent UUID in that same
scope. Each normalized block has its own stable event ID. These are message-tree
claims, not parent sessions or task roots. Main/sidechain writers stay distinct;
root independence, parent-session identity and lineage remain unknown. Moving the
file or editing omitted bodies preserves metadata identities and monitor
fingerprints. Identical UUID observations deduplicate; incompatible approved UUID
metadata rejects. Parent cycles are never followed.

Assistant `message.model` becomes `reported_model_fingerprint`, preserving a native
report without authenticating the served model or treating it as a requested alias.
Response IDs and stop reasons are fingerprinted. Response facets use the actual
supporting snapshot's timestamp/message metadata. A consistent native stop marker
can produce `response_stop`; it does not mean the session or task completed.
Unknown execution settings, memory injection, served version and outcomes stay
unknown, including when native input supplies forged grading/activation labels.

Only a complete, unambiguous native `message.usage` receipt with a response ID emits
`input_tokens`/`output_tokens`, once per response. These are observed uncached
counts; cache creation/read counts are validated but not added or priced. Repeated
consistent snapshots do not add usage. Every known partial count and reported
model participates in consistency checks: contradictory values withhold the facet
and emit coverage, while separate incomplete snapshots cannot fabricate one
complete measurement. Missing IDs/counts stay unattributed. This is neither a cost
estimate nor proof of final billed usage.

Pinned Codex and Claude files share guarded streaming JSONL I/O. There are no
implicit file-byte, line-byte, record, block-count or time ceilings. Native reader
APIs enforce caller-selected `max_bytes`, `max_line_bytes`, `max_records` and an
absolute monotonic `deadline`; omitted limits are `None`. Explicit record limits
also check event/facet expansion before allocation. A repeated UUID gets only its
original per-row temporary
validation allowance; it does not consume unique output capacity again. Derived
pending-call coverage is also subject to the final normalized limit. An uncommitted
last line, even valid JSON, is omitted with partial coverage. Malformed complete
lines, duplicate JSON keys, unsafe links, selected-input mutation, invalid counts
or hard resource expiry reject before emitting any ledger events. The explicit
profile does not create a source-journal checkpoint or resolve external histories.

`task test:captures:claude` runs the synthetic profile cases. Both README suites
and installed wheel/archive checks include them. Broader Claude discovery, optional
sidecar-parent resolution, source-journal receipts and live runtime validation
remain open in #44; none of the synthetic checks establish agent-performance gains.

### Declared loading signals

The pinned profile also records which harness files a capture names, as
`loading_signal` metadata. `claude_native.CAPABILITY` declares
`loading_signal: observed_pattern` and `effective_loading: False`.

| Native shape | Event | Added fields |
| --- | --- | --- |
| `attachment` entry, `attachment.type: nested_memory`, absolute `attachment.path` | `context` | `loading_signal: observed_attachment`, `loading_evidence: observed_pattern`; `loading_path_sha256` when the path is exactly below `PROJECT`; `loading_content_sha256` when `attachment.content.content` is a string |
| `attachment` entry, `attachment.type` `hook_success`, `hook_additional_context`, `hook_non_blocking_error` or `hook_blocking_error` | `context` | `loading_signal: observed_hook_output`, `loading_evidence: observed_pattern` |
| `Agent` or `Task` call with a nonempty string `input.subagent_type` | `tool_call` | `loading_signal: declared_agent_invocation`, `agent_name_sha256` |
| `mcp__<server>__<tool>` call with exactly two non-empty parts and a `<server>` not starting with `plugin_` | `tool_call` | `loading_signal: declared_mcp_invocation`, `mcp_server_sha256` |

`loading_path_sha256` is `schema.digest` of the project-relative path, the same
value as an inventory node's `path_sha256`. The path follows the exact-spelling
rule of [declared skill signals](#declared-skill-signals). A path outside
`PROJECT`, such as a user memory file, keeps the signal without a path digest.
`loading_content_sha256` is the SHA-256 of the attached text encoded as UTF-8.
`agent_name_sha256` is `digest(name)`, the `name_sha256` that the agent inventory
records. `mcp_server_sha256` comes from `config_inventory.telemetry_server_sha256`,
and the MCP tool name stays an opaque `tool:` digest. A plugin MCP name
(`mcp__plugin_...`) or a name with more or fewer `__` parts gains no
`loading_signal`, because its server id cannot be read from it unambiguously. Paths, text, hook names and
prompts are never copied.

**Assumption.** The pinned SDK reference names the `attachment` entry type but not
its payload. These shapes are unverified and supported only by synthetic fixtures.
A rule file is expected as a `nested_memory` attachment with its own path. Every
other attachment type, a relative path or a `content.path` that differs from
`path` stays `unsupported_native_entry`. Claude Code 2.1.317 captures are not
supported, because no documented attachment shape is pinned for that version.

**Not established.** An attachment shows that the runtime named a file in its
context, not that the model followed it. An agent or MCP request is a model
request. Hook output carries no file path, so it cannot name a hook file. No
signal sets an outcome, skill activation or independence label. Captures without
these shapes keep their events, identities and fingerprints. An `Agent` or `Task`
call with `subagent_type` and an MCP call whose name matches the row above gain
the fields above, so their fingerprints change; other MCP calls are unchanged. The pinned Codex rollout records no loading evidence:
`codex_rollout.LOADING_SIGNAL` is `no_native_loading_signal`, and its events are
unchanged. [Native loading attribution](source-contracts.md#native-loading-attribution)
joins these signals to inventory nodes.

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

The profile requires an explicit file or source root, a matching `cli_version`, one session header
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
compressed capture and source-journal execution receipts remain open in #44/#58.

Safe POSIX reads reject links/special files and detect concurrent source changes.
Reads stream individual JSONL records without an implicit file or line ceiling;
only caller-selected byte, record or deadline limits apply. A final line without its
newline is omitted even when its JSON parses; the complete prefix and partial-tail
warning remain visible. Malformed complete records, duplicate keys, ordinal/usage
conflicts and unavailable safe filesystem APIs fail explicitly. Capture remains
partial, including after native completion. No source-journal cursor is advanced
by this explicit analysis path. `task test:captures:codex` runs the native projection,
privacy, ownership and CLI cases; wheel/archive tests exercise it after installation.

### Bounded source-root and session selection

```sh
ahl analyze PROJECT --source-root NATIVE_ROOT --format codex \
  --source-version codex-rollout-0.166.0 --session NATIVE_THREAD_ID \
  --max-captures 100 --max-capture-scan 2000 --max-events 100000
```

The root is an explicitly authorized directory, including any selected active and
archive subdirectories. It is mutually exclusive with `--events`; `--session`
requires the root and matches an exact native thread `id`, not a root-session ID
or filename. Omit it to include matching project captures within the bounds. Codex
root selection requires the pinned Codex profile and never falls back to home discovery.
Only uncompressed `rollout-*.jsonl` files qualify. Prompt indexes such as
`history.jsonl` and compressed captures are not treated as transcripts.

Native root discovery walks iteratively within bounds of 128 directory levels and
250,000 visited filesystem entries (`ahl analyze --source-root` always uses these
defaults; the source-journal descriptor's optional `max_walk_entries` can change
the entry bound, up to 5,000,000); reaching one is reported in
`reached_limits` (`scan_depth`/`scan_entries`) and `walk_limits`, with an
incomplete-history caveat. It has no implicit file-count, record-count, byte or
time ceiling. Caller-selected scan/accepted-file and record
limits remain enforced and reported; `None` means unlimited. Optional deadlines
are checked during read, parse, correlation and final verification. Foreign-project
and other-session
headers are filtered before full reads and before consuming the accepted-file
allowance. Unsupported/incomplete headers and skipped unsafe entries are counted;
symlinks, hardlinks and special files are never followed/opened as captures.
Candidates within the bounded scan use descending rollout-filename order; this
admission order does not establish execution time or concurrency.

Scan/accepted-file cutoffs are visible coverage limits. Hard read, record,
filesystem and stability violations abort before any partial ledger is emitted.
Foreign headers excluded after their stable prefix read are not dependencies of
the selected-file snapshot. Every admitted file remains stable through final
verification. No matching captures produces an explicit unknown-coverage warning,
not a successful empty history. All source and per-file paths remain fingerprints.

Active/archive copies and overlapping prefixes are merged by scoped native event
identity before tool correlation. A result in a longer copy can satisfy a call
from a shorter prefix without retaining a spurious unmatched-call observation.
Paginated `capture_order` retains the native ordinal; legacy captures retain line
order. Conflicting approved metadata/root claims reject the merge. Suppressed
duplicate usage retains its ordinal claim for conflict detection.

`native_response_fingerprint` identifies own-thread usage receipts. Repeated
response IDs with consistent turn/root-turn/count metadata contribute usage once,
including when repeated at different ordinals; conflicting usage rejects. Distinct
thread identities remain separate despite matching text, and inherited parent
usage is excluded rather than added to child usage. Root/parent declarations still
do not prove independence or certify execution. The private source summary records
per-file digests, limits, exclusions and duplicate counts; metadata-identical archive
copies do not change the monitor's event fingerprint. No source cursor is advanced.

Native source journaling can also use the explicit root profile described in
[source contracts](source-contracts.md#explicit-bounded-root-journal). It reuses
this discovery path with stricter raw revision consistency and checkpoint admission;
ordinary telemetry keeps metadata-only body handling and response-usage counts.
Suppressed duplicate usage still participates in cross-file ordinal consistency.

Unlimited selection does not remove host constraints. Each JSONL record must fit
the host JSON decoder and memory, and normalized events and correlation state are
retained in memory. File I/O streams records instead of loading a whole capture;
filesystem access, scope, stable-read and provenance checks still apply.

### Bounded Claude project-root selection

```sh
ahl analyze PROJECT --source-root CLAUDE_PROJECTS_DIR --format claude \
  --source-version claude-jsonl-2.1.316 --session NATIVE_SESSION_UUID \
  --max-captures 100 --max-capture-scan 2000 --max-events 100000
```

The pinned Claude profile accepts one explicitly authorized directory with the
layout of a Claude `projects` directory. It reuses the reader of the
[bounded Claude project-root journal](source-contracts.md#explicit-bounded-claude-project-root-journal):
only the directories that `PROJECT`'s sanitized path maps to are listed and read,
the main `<session-uuid>.jsonl` and its `<session-uuid>/subagents/**/agent-*.jsonl`
transcripts are candidates, and each candidate's first attributable envelope
(`version`, `cwd`, `sessionId`, `isSidechain`, `agentId`) must agree with its
location before the whole file is replayed. Other project directories are never
opened; a colliding project's file in the mapped directory is rejected by record
scope. There is no home, `CLAUDE_CONFIG_DIR` or account discovery, and this
selection never falls back to default discovery. `--session` requires the root and
an exact native session UUID; it selects that session's main and subagent
transcripts. `--max-captures`, `--max-capture-scan` and `--max-events` map to the
journal's `max_files`, `max_scan_files` and `max_records`; omitted limits are
unlimited.

Main and subagent writers remain separate cases; sidechain owners, parent
sessions, independence and outcomes stay unknown, and parent pointers are not
traversed. Copies of one writer in the selected directories merge by native UUID.
The private source summary keeps per-file receipts, digests, counts and reached
limits as fingerprints. A reached file, scan or subagent-tree limit, an
unattributable, unsupported, mislocated or unrecognized-layout file, or a skipped
unsafe entry adds an incomplete-history caveat; a missing or empty mapped directory
adds the unknown-coverage caveat instead of an empty success. A partial last line
is omitted with its caveat. Hard read, record and stability violations abort before
any ledger event. Only one root is accepted here; an archive or backup copy is
analyzed separately or declared as an additional root in the source-journal
profile. Analysis advances no source cursor, and the closed-input and bank
requalification domains still accept only the pinned Codex root.
