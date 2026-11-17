# ATIF import reference

`ahl trace-import` converts one [ATIF](https://docs.harborframework.com/agents/atif) trajectory
into AHL telemetry metadata, as described in [import an ATIF trajectory](import-atif-trajectory.md).
The pinned source schema is **ATIF-v1.8**, from the
[ATIF RFC](https://github.com/harbor-framework/harbor/blob/74cc6312018c349c6bd2400c89a0ac4983ac1085/rfcs/0001-trajectory-format.md).
Full ATIF export and OpenTelemetry output are not part of this command.
Tool-input fingerprints preserve equality within a case and are not anonymization:
a reader with the case identity can test guesses for low-entropy input values.

## What this is not

AHL's ledger keeps no message bodies, tool arguments or results, or reasoning, while an ATIF
trajectory holds all of them. The import is therefore a lossy, one-way, metadata-only
conversion. It cannot be advertised as, or turned back into, a faithful trajectory, and absent
content is never reconstructed. Interoperability grants no execution, grading or promotion
authority.

## Command

```text
ahl trace-import --format atif --input FILE --output NEW_DIRECTORY
```

| Option | Meaning |
| --- | --- |
| `--format` | Source format. Only `atif` exists. |
| `--input FILE` | One existing regular, single-link file of at most 32 MiB, opened without following links and read once. |
| `--output NEW_DIRECTORY` | A directory that does not exist, under an existing parent. It is created atomically with mode `0700` and holds `events.v2.jsonl` and `conversion-report.json`, mode `0600`. |

Exit `0` converted, with the report on stdout; exit `2` rejected, with `trace-import rejected:
REASON at PATH` on stderr. Refusals before publication leave no output;
`output_durability_unknown` means the output was published but its parent-directory sync
failed. A failed publication may leave a private staging directory beside the requested output;
unexpected files in it are never deleted. The command reads nothing but `FILE` and
opens no socket. Atomic publication requires Linux `renameat2` with
`RENAME_NOREPLACE`; where it is unavailable, the command fails closed without publishing.
Use a parent directory that other processes cannot modify during import: checking the
staging directory before rename cannot prevent a same-user writer from swapping it
between the check and the rename.

## Limits

Each limit rejects the document; none truncates it.

| Limit | Value | Reason |
| --- | --- | --- |
| Input bytes | 32 MiB | `input_too_large` |
| JSON nesting | 32 levels, checked over the whole document | `nesting_limit_exceeded` |
| Steps per trajectory | 10 000 | `step_limit_exceeded` |
| Embedded subagent trajectories | 64 | `subagent_limit_exceeded` |
| Events written | 500 000 | `event_limit_exceeded` |

## Field mapping

Paths are shown for the root trajectory; embedded subagent trajectories use the same paths. A
field marked *retained* reaches the ledger as a typed value, *digest* only as a content-free
digest, *presence* only as the fact that it existed, and *dropped* never. Dropped fields are
still validated and counted in the report. A null optional field is treated as absent. A field the table does not list is *undeclared*: it
is dropped and counted under `PREFIX.<undeclared>`, and its name is never recorded.

| Source field | Treatment | AHL destination and rule |
| --- | --- | --- |
| `trajectory.schema_version` | retained | `source_version`; must equal `ATIF-v1.8` |
| `trajectory.session_id` | dropped | Not used. Case identity comes from the source digest, so no grouping across files is asserted |
| `trajectory.trajectory_id` | digest | Required on an embedded subagent; unique across the file; resolves references |
| `trajectory.notes` | dropped | |
| `trajectory.continued_trajectory_ref` | dropped | Counted as `continuation_reference_not_followed`; never followed |
| `trajectory.extra` | dropped | |
| `trajectory.subagent_trajectories` | container | Each element becomes its own case under the same `root_case_id`; nesting inside an element is rejected |
| `agent.name`, `agent.version` | digest | `runtime_fingerprint` |
| `agent.model_name` | digest | `requested_model_fingerprint` |
| `agent.tool_definitions`, `agent.extra` | dropped | |
| `final_metrics.total_prompt_tokens`, `final_metrics.total_completion_tokens`, `final_metrics.total_cached_tokens`, `final_metrics.total_cost_usd`, `final_metrics.total_steps`, `final_metrics.extra` | dropped | Validated, never added to per-step usage, so a total cannot be counted twice |
| `steps[].step_id` | retained | Must be `1..n` in order; sets event order and event ids |
| `steps[].timestamp` | retained | `timestamp`; must carry a time zone. A missing timestamp stays missing and no duration is derived |
| `steps[].source` | retained | `user` is `user_message`, `agent` is `agent_message`, `system` is `context`; also `actor` |
| `steps[].model_name` | digest | `reported_model_fingerprint` |
| `steps[].message` | dropped | Text or typed parts. `image` and `audio` parts are counted as unsupported and their locations are never read |
| `steps[].reasoning_content` | presence | One `reasoning` event when non-empty |
| `steps[].reasoning_effort`, `steps[].extra`, `steps[].llm_call_count` | dropped | A step with `llm_call_count` above 1 raises the warning `aggregated_llm_calls_in_one_step` |
| `steps[].is_copied_context` | presence | A `true` step is not imported as a new interaction; `null` is treated as a new interaction and is counted as `copied_context_steps_not_imported` |
| `steps[].tool_calls[].tool_call_id` | digest | `call_id`, scoped to the case |
| `steps[].tool_calls[].function_name` | digest | `tool_name` as `tool:` plus a digest |
| `steps[].tool_calls[].arguments` | digest | `input_fingerprint`, a case-salted 64-bit fingerprint |
| `steps[].tool_calls[].extra` | dropped | |
| `steps[].observation.results[].source_call_id` | digest | `call_id` of a `tool_result`; absent leaves the result unlinked with `missing_reason` `source_call_id_absent` |
| `steps[].observation.results[].content`, `steps[].observation.results[].extra` | dropped | |
| `steps[].observation.results[].subagent_trajectory_ref[].trajectory_id` | digest | Resolves to an embedded trajectory and sets its parent link |
| `steps[].observation.results[].subagent_trajectory_ref[].trajectory_path`, `steps[].observation.results[].subagent_trajectory_ref[].session_id`, `steps[].observation.results[].subagent_trajectory_ref[].extra` | dropped | A path is counted as `subagent_trajectory_path_references` and never followed |
| `steps[].metrics.prompt_tokens`, `steps[].metrics.cached_tokens` | retained | `input_tokens` is prompt minus cached, because AHL counts uncached input. Without `cached_tokens` there is no input count and the event says `cached_token_split_unavailable` |
| `steps[].metrics.completion_tokens` | retained | `output_tokens` |
| `steps[].metrics.cost_usd`, `steps[].metrics.prompt_token_ids`, `steps[].metrics.completion_token_ids`, `steps[].metrics.logprobs`, `steps[].metrics.extra` | dropped | Cost is never carried or estimated |

## Events

Events follow the [telemetry contract](telemetry.md) with `source_format` `atif`,
`source_version` `ATIF-v1.8`, `capture_complete` false and `lineage_verified` false. Per step
they appear in this order: message, reasoning, tool calls, tool results, usage, then coverage
events for unsupported media and subagent references.

Call and result events are correlated by the same rule as native captures: a result whose call
is unknown is `unmatched_result`, a repeated call identifier is `duplicate_call`, and a call
that never returns ends the stream with a `capture_coverage` event `unmatched_call`. A result
that arrives before its call is not reordered into a link. There is no cancellation or error
status in ATIF, so `execution_status` is `unknown`.

A subagent embedded in the file is a separate case with the same `root_case_id`. It gets
`native_parent_thread_fingerprint` only when exactly one trajectory references it. Otherwise the
report warns `subagent_parent_unknown` or `subagent_parent_ambiguous`. Usage in a parent and in
a subagent stays in separate cases and is never summed.

Outcome stays `unknown` with provenance `unknown`. Nothing in an ATIF document, including
`extra`, can set a grade, a verified lineage or independence.

## Conversion report

| Field | Meaning |
| --- | --- |
| `source_version`, `target_version`, `converter` | The pinned ATIF version, `ahl-telemetry-event-v2` and `ahl-atif-import-v1` |
| `source_digest` | SHA-256 of the exact input bytes |
| `lossy`, `round_trip` | `true` and `retained_metadata_only`: only retained metadata survives a round trip, and omitted content is not claimed to |
| `retained_fields` | Paths of retained, digested and presence fields that occurred |
| `dropped_fields` | Path to the number of times a dropped or undeclared field occurred |
| `missing_fields` | Path to the number of places a field AHL maps was absent |
| `unsupported_features` | Feature to count: `image_content_parts_not_loaded`, `audio_content_parts_not_loaded`, `subagent_trajectory_path_references`, `unresolved_subagent_references`, `continuation_reference_not_followed`, `copied_context_steps_not_imported` |
| `warnings` | `undeclared_fields_dropped`, `aggregated_llm_calls_in_one_step`, `subagent_parent_unknown`, `subagent_parent_ambiguous` |
| `counts` | `trajectories`, `steps`, `events` and one `status:` count per linkage state |
| `outcome`, `independence`, `lineage_verified` | Always `unknown`, `unknown` and `false` |
| `publication_allowed`, `network_used`, `referenced_files_read` | Always `false` |

The report holds paths, counts and the source digest. It never holds a value from the input.

## Rejection reasons

| Reason | When |
| --- | --- |
| `input_unavailable` | The file is missing, a link, not a single regular file, or unreadable |
| `input_too_large` | The file exceeds the byte limit |
| `invalid_json`, `duplicate_json_key`, `nonfinite_number` | Not UTF-8 JSON, a repeated key, `NaN` or `Infinity` |
| `nesting_limit_exceeded` | Nesting beyond the limit |
| `unsupported_schema_version` | Anything but `ATIF-v1.8`; the refusal names a recognizable version it found and otherwise says `unrecognized` |
| `invalid_structure`, `missing_field` | A wrong type or shape, or a required field absent; the path is a schema path |
| `invalid_timestamp`, `invalid_measurement` | A timestamp that is not ISO 8601 with a zone, or a count or cost that is negative, fractional, boolean, nonfinite or larger than 2^63-1; also a cached count above its prompt count |
| `non_sequential_step_ids` | Step identifiers are not `1..n` in order |
| `unsupported_content_part` | A part that is not `text`, `image` or `audio`, or is malformed |
| `duplicate_trajectory_id`, `unsupported_nesting` | A repeated embedded identifier, or subagent trajectories nested inside a subagent |
| `invalid_subagent_reference`, `subagent_reference_cycle` | A reference with neither `trajectory_id` nor `trajectory_path`, or a reference cycle among embedded trajectories |
| `step_limit_exceeded`, `subagent_limit_exceeded`, `event_limit_exceeded` | A count limit |
| `output_exists`, `unsafe_output`, `output_unavailable` | The output already exists, crosses a link or `..`, or cannot be created |
| `output_durability_unknown` | The output was published, but syncing its parent directory failed; inspect it before retrying |
| `unsupported_format` | The requested format is not implemented |

## Evidence status

The mapping was checked against the RFC text and against synthetic fixtures. The importer is not
validated against Harbor's own validator, and no model was run. An importer test is not
fresh-model evaluation evidence.
