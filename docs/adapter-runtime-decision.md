# Decision: where a tool-result adapter may attach to a runtime

Status: accepted for the offline identity bridge; native integration stays
unsupported until a separately authorized live verification observes the bridge
running. Investigated on 2026-11-12 against public documentation only; no
runtime was launched and no model was called.

This record answers the adapter issue's pre-implementation questions. The
capability contract (`AdapterSpec`) and the identity bridge it constrains are
described in [Controlled experiments](controlled-experiments.md).

## Context

An opt-in adapter may only present an already authorized tool result. It must
keep the original status and payload, cannot call a tool, retry or suppress a
call, and must run with no oracle, credentials or network. A runtime qualifies
only if it offers a documented extension point after the tool has run. AHL does
not patch runtimes, mount global configuration or bypass permissions to create
one.

## Evidence

| Runtime | Documented point after a tool result | Coverage | Version | Limits that matter here |
| :- | :- | :- | :- | :- |
| Claude Code | `PostToolUse` and `PostToolUseFailure` command hooks ([hooks reference](https://code.claude.com/docs/en/hooks)) | Built-in and MCP tools, matched by tool name | `updatedToolOutput` replaces output for all tools since 2.1.121 ([changelog](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md)); AHL pins 2.1.317 | Replacement exists only on `PostToolUse`, which fires after a successful call. `PostToolUseFailure` accepts `additionalContext` only. A replacement that does not match a built-in tool's output shape is ignored and the original output is used. The hook is a host command, not a process AHL isolates. Telemetry records the original output before the hook runs. `PostToolBatch` hands a hook the result the model receives, which is a documented way to observe delivery |
| Codex | `PostToolUse` command and MCP-tool hooks ([hooks reference](https://developers.openai.com/codex/hooks)) | Bash, `apply_patch`, MCP and other local function tools | Page is not version-pinned; unverified for the AHL pin | A hook can only replace a result with its own feedback text, using block or stop semantics. `updatedMCPToolOutput` is parsed but not supported yet. That cannot preserve the original payload or status |

Both claims are documentation reads. None was exercised against a live runtime.

## Decision

1. Claude Code `PostToolUse` and `PostToolUseFailure` command hooks are the one
   designated integration. The identity bridge answers every event with an empty
   hook reply, so the runtime keeps the original output; it replaces nothing.
2. Codex is unsupported: no documented hook returns a transformed tool result
   while preserving its status.
3. A non-identity transform is out of scope until the blockers below are
   closed. The first transform, structured error presentation, can only be
   delivered as additive context on `PostToolUseFailure`. That context reaches
   the model as a system reminder, so any text derived from tool output needs a
   fixed vocabulary or a bounded, escaped form before it may be used.
4. `runtime_capability()` keeps reporting `unsupported`. For Claude Code it adds
   the documented candidate and its open blockers as data, so a run can detect
   the gap before evaluation instead of finding it afterwards.
5. A fake runtime in tests proves insertion, ordering and provenance. It is
   never evidence that the supported runtime was exercised.

## Ownership, isolation and fallback

- The authorization manifest, adapter digest, hook routing and executable path
  belong to the controller. Hook declarations in a skill or agent frontmatter, or
  anywhere an optimizer can edit, are not an acceptable source. Deploying the
  hook configuration is the hook-deployment contract's job, not this bridge's.
- The identity bridge runs in the controller process and executes no adapter
  bytes. It still requires the exact declared bytes, so the isolated worker
  starts from a hash-checked artifact. `evaluation.adapter_worker` runs those
  bytes in the offline sandbox with no network, host home, credentials or oracle
  and verifies the answer against the original envelope. A hook command runs
  with the user's privileges; the isolation boundary for a transforming worker
  must be a controller-owned relay from the hook to that worker, and the relay is
  not built or verified. The worker is exercised only directly, never through
  a hook.
- A required bridge stops the affected execution (`continue: false`) on any
  malformed, oversized, unauthorized, mismatched or conflicting event. An
  optional bridge may only fall back to identity, the policy is frozen before the
  run, and each fallback is recorded. Neither path turns an error into a success.

## Open blockers (external evidence)

Each of these needs a live runtime run under separate authorization, plus
independent grading for any effectiveness claim:

- A live run showing the bridge hook executes and that the model-visible result
  equals what the bridge returned, observed through a separate hook.
- A decision on how failed calls are presented, given that replacement is not
  available there.
- A containment check for the relay, with secret, oracle and network canaries
  (the worker's own sandbox canaries run on a host with bubblewrap and are
  skipped, never counted as passed, where it is missing).
- A matched identity, prompt-only and adapter comparison; fewer calls or tidier
  output is not an adoption criterion.
