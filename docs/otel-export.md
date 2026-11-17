# Export private metadata as OTLP logs

`ahl trace-export --format otel-json --input RUN_DIRECTORY --output NEW_DIRECTORY`
reads only `events.v2.jsonl` in the named directory and creates `otel-logs.json`
and `conversion-report.json` in a new private directory. Input and output must
not overlap. The input is limited to 32 MiB and 100,000 events. The output parent
must not be modified concurrently by another process running as the same user.
The command does not open a socket, inspect referenced files, or use ambient
collector configuration. Exit 2 reports a rejection; `output_durability_unknown`
means publication occurred but syncing its parent failed. Failed publication may
leave a private staging directory.

The wire schema is [OpenTelemetry protobuf v1.11.1 LogsData](https://github.com/open-telemetry/opentelemetry-proto/blob/v1.11.1/opentelemetry/proto/logs/v1/logs.proto),
encoded using OTLP JSON. This is a log representation, not a trace: it creates no
spans, trace IDs, parent links, or start/end times. A missing or invalid timestamp
is omitted, not replaced by zero. It uses AHL custom `ahl.*` attributes and no
standard GenAI semantic attributes (baseline [semantic conventions v1.37.0](https://github.com/open-telemetry/semantic-conventions/tree/v1.37.0)): the source cannot establish that usage is
unique per model invocation. No message, reasoning, tool name, argument, result,
cost, or arbitrary extension content is exported. A task outcome stays unknown
and no execution status establishes correctness or independent lineage.

| Source field | OTLP representation |
| --- | --- |
| `case_id`, `event_id` | Domain-separated SHA-256 identifiers in `ahl.case.sha256`, `ahl.event.sha256` |
| `activity`, `capture_status`, `execution_status` | Allowlisted `ahl.*` labels, otherwise `unknown` |
| `capture_order` | `ahl.capture.order` when representable as a signed 64-bit integer |
| `timestamp` | `timeUnixNano` when an explicit valid offset and representable value exist |
| `input_tokens`, `output_tokens` | `ahl.observed.*` on `response_usage` only, when representable; no summation is implied |
| `elapsed_seconds` | `ahl.observed.elapsed_seconds` when representable |
| Other schema fields or undeclared keys | Dropped and counted in the conversion report |

Identical event identities and contents are emitted once; conflicting identities
are rejected. The report counts duplicate inputs, omitted fields and measurements,
unsupported activities, missing and invalid times, and explicitly records that
publication is not allowed. Consumers must not sum usage across events without
separate evidence that the measurements are disjoint.
Null and undeclared field counts cover all source records, including duplicates;
other omitted field counts cover distinct exported records.

For the supported round-trip, parse the output as the pinned `LogsData` protobuf
and serialize it back to OTLP JSON: the projected attributes and timestamps
survive. Original identifiers and dropped content cannot be reconstructed. The
identifiers and source digest are hashes, not anonymization: low-entropy values may be guessed.
The files and even their aggregates remain private and are not authorized for
publication.
