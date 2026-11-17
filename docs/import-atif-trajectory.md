# Import an ATIF trajectory as AHL metadata

Use this when an agent run produced an [ATIF](https://docs.harborframework.com/agents/atif)
`trajectory.json` and you want AHL to analyze its tool-call structure, ordering and token
counts without keeping the conversation. `ahl trace-import` reads one file you name and writes
a new private directory of metadata events and a loss report. It calls no model, opens no
network connection and follows nothing the trajectory refers to. Every field and reason is in
the [ATIF import reference](atif-import-reference.md).

## Before you start

- The trajectory must declare exactly `ATIF-v1.8`. Any other schema version is rejected and the
  refusal names the version it found, so you know which export to ask for.
- You must be allowed to analyze the file. The import is private evidence: converting a
  trajectory does not make it publishable, and hashes are not redaction.
- The output directory must not exist yet.

## Import it

```sh
ahl trace-import --format atif --input trajectory.json --output ./imported-run
```

The command prints the loss report as JSON and creates:

| File | Contents |
| --- | --- |
| `imported-run/events.v2.jsonl` | One content-free telemetry event per step, reasoning marker, tool call, tool result and usage receipt |
| `imported-run/conversion-report.json` | What was kept, dropped, missing and unsupported, by field path and count |

Both files are mode `0600` in a `0700` directory. The input file is read once and is not
copied.

## Read the report before you trust the events

Open `conversion-report.json` and check three things:

1. `dropped_fields` lists the message bodies, tool arguments and results, costs and `extra`
   objects that were not kept. Their values are never recorded, only how many there were.
2. `missing_fields` and `warnings` show what the trajectory did not say, such as steps without a
   timestamp or a prompt total without its cached share. Missing stays missing; AHL does not
   substitute a zero or an estimate.
3. `unsupported_features` shows constructs AHL did not interpret: image or audio parts, subagent
   trajectories referenced by file path, a continuation reference. They were counted and never
   opened.

A call that has no result shows up as a `capture_coverage` event with status `unmatched_call`.
That means the evidence is incomplete; it does not mean the call failed or succeeded.

## Analyze the imported events

```sh
ahl analyze . --events imported-run/events.v2.jsonl --format normalized
```

Imported events never carry a task result. The outcome stays `unknown` and no lineage is marked
verified, so an imported trajectory cannot count as a pass, a failure or an independent root. To
grade it you need a separate reviewed grader or annotation.

## When the import is rejected

The command exits `2`, prints `trace-import rejected: REASON at PATH` and writes nothing. The
reason is one of the typed codes in the [reference](atif-import-reference.md#rejection-reasons).
Fix the input or ask the producer for an `ATIF-v1.8` export; do not edit the trajectory to make
a limit pass.
