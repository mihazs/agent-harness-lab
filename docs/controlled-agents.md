# Controller-loaded instructions

Controlled Responses runs can opt into `ahl-controlled-agents-v1` by adding
`"instruction_protocol": "ahl-controlled-agents-v1"` to the existing inference
settings JSON. Omit the field for the existing mounted-harness behavior. `null`
and other protocol names are rejected. This does not configure or launch Codex
or Claude and does not override an offline project policy.

The controller freezes the admitted harness snapshot, including root
`AGENTS.md` bytes, mode and membership. Executor developer context includes its
strict UTF-8 content in a deterministic envelope before the first request and
retains that prefix on every continuation. Removing the file produces an
explicit absent envelope with empty content. No normalization, truncation,
skill loading or `CLAUDE.md` activation is implied. Preparation roles keep their
existing policies and input views; candidate instruction content is not
injected into their requests.

A manifest task may set `cwd`, a safe relative directory. The executor's context
is then the `AGENTS.md` chain from the harness root down to `cwd`, one file per
directory in that order, each recorded as present or absent. `cwd` requires this
protocol in the inference settings. Preflight rejects a `cwd` task without it,
and rejects a frozen baseline or candidate chain over 32 KiB or with a
non-regular member, before credentials, inference or output. The chain is never
truncated. `AGENTS.override.md` and fallback names are not modelled. Tasks
without `cwd` keep the root-only context above.

The existing request checkpoint and action records contain digests of the
actual serialized request, initial context, settings, requested tool policy,
frozen inputs and worker image. Records include role, request ordinal and the
actual trial/run binding when a comparison is active. Project configuration is
bound when selected. Raw request bodies and injected instruction text are not
added to these records. All records remain private.

The current process-owned client and role handles reserve attempts before
transport and complete them only after protocol validation. The current Scope
requires contiguous coverage of all provider attempts and joins requests to
owned native input/image facts and artifact grades. A comparison retains those
same request bindings alongside its frozen schedule and worker custody. Saved
JSON, copied clients, retired role contexts and local replay cannot supply live
request authority. New clients cannot restart an existing Scope's budget.

Context, model declaration, tool policy, settings and deadline changes fail
closed. Request and metadata bounds are checked before dispatch; ambiguous
transport retains the spent reservation, poisons the client and is not retried.
The parent deadline, counters, writer and existing kernel completion are shared;
this protocol introduces no recovery ledger or additional model execution.

A qualified `controlled_instruction_protocol` means controller injection and
transmission through the selected transport with a validated response, joined
to the observed native worker/grader path. It does not establish provider-side
processing, physical served-model identity, instruction adherence, native
activation, scientific independence or full certification. Existing application
and certification gates remain in force. Cold resume remains unsupported.

`task test:evaluation:instruction-protocol` runs native unit controls.
`task test:evaluation:instruction-protocol:kernel` uses the delegated Linux
fixture, real workers and artifact graders with synthetic wire responses. The
synthetic provider chooses commands from the actual injected message content;
workers do not read AGENTS.md to choose their behavior. No provider, credentials,
model download or native-agent session is used by these tests.
