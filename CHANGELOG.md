# Changelog

## 0.14.0

### Minor Changes

- Add opt-in quota-backed private storage for offline sandbox workers and graders. Verify bounded work, temporary and shared-memory volumes, seed fixtures through a private directory handoff, and export only bounded declared artifacts after whole-tree cleanup. Require explicit cgroup controls and keep automatic promotion disabled.

## 0.13.0

### Minor Changes

- Add opt-in SHA-256-pinned extracted runtime images to offline executors and pristine graders. Inspect a bounded private rootfs without executing image code, bind its complete content/permission/ownership/mtime/link/xattr identity, reject unsafe files and role-data overlap, and verify integrity before and after each worker. Expose explicit image flags and a Taskfile pin command; unpinned diagnostics remain labeled and automatic promotion stays disabled.

## 0.12.1

### Patch Changes

- Fix an oracle-read bypass in the starter Python grader: sandboxed submitted code now runs in a nested namespace without oracle or grader-process access. Version the frozen graders as v2 while preserving the explicitly trusted cooperative diagnostic path.

- Add opt-in native kernel canaries in a disposable foreground systemd user scope, including real role/file/network boundaries, pristine regrading, aggregate cgroup resources and offline paired evaluation. Support source, wheel and archive layouts without persistent services or changing runtime defaults. Document per-user process-limit failures and keep unsupported tests explicit; full isolation certification remains pending.

## 0.12.0

### Minor Changes

- Add opt-in delegated Linux cgroup v2 controls for offline executors and pristine graders. Verify aggregate memory/process caps and CPU bandwidth policy before launch, attach before exec, kill the whole descendant tree and require complete accounting before attestation. Missing delegation or cleanup failure never falls back to a weaker boundary. Kernel enforcement canaries require an explicit delegated host; disk quotas, inference brokering and full role isolation remain pending.

## 0.11.0

### Minor Changes

- Add an opt-in stateless JSON MCP reader for ai-memory memory_query and memory_read_page. Require explicit scope/authentication and pinned server/tool schemas, reject mutators/global queries/stateful or SSE-only responses, bound discovery and payloads, and redact failures without retries. Reuse bounded HTTPS exchange logic with the existing GET transport. Remote CLI activation and normalized MCP journal ingestion remain pending.

## 0.10.0

### Minor Changes

- Read explicitly selected extracted ai-memory OKF v0.2 project bundles with an optional YAML extra. Preserve parsed page metadata in memory, import fingerprinted contextual evidence through atomic checkpoints, and report freshness, authorship and lifecycle claims without treating summaries as execution or review proof. Reject unsafe YAML, links, special files, nested scopes and project mismatches; never read the derived database or start servers/hooks.

## 0.9.0

### Minor Changes

- Add an explicitly authorized HTTPS GET transport for source adapters with TLS verification, same-origin/path pagination, bounded pages/records/bytes, timeouts and cooperative deadlines. Reject redirects, rate limits, malformed/partial responses, cursor cycles and credential-bearing URLs without automatic retries. Provider decoders stay explicit; no remote connector is enabled by installation or discovery.

## 0.8.0

### Minor Changes

- Add explicit offline source probe, preview, import and status commands with version-gated registry dispatch. The local normalized-metadata export connector checks declared project/scope and root identity before reading bounded record data, freezes file snapshots and imports through atomic checkpoints. Default discovery enables no sources; provider APIs and native session formats remain pending.

## 0.7.0

### Minor Changes

- Add a bounded private metadata journal with atomic record/checkpoint commits, source-scoped revision idempotency, preserved tombstones and explicit supersession claims. Reject stale or cyclic cursors, conflicting revisions and cross-project cache use; report ambiguous filesystem commits for inspection. Keep unknown capture and grading authority unchanged. Centralize reusable development commands in Taskfile and replace standalone packaging smoke commands with native unittest cases shared by source, wheel and archive layouts.

## 0.6.0

### Minor Changes

- Migrate the zero-clone staged pre-commit gate to Lefthook. Explicit guarded hook installation preserves foreign hooks and migrates the repository's legacy hook path; automatic npm lifecycle installation is disabled.
- Install Lefthook explicitly in release automation and run feature CI once per pull request, retaining all required checks and main-branch validation.

- Define strict metadata-only source, evidence and import-receipt contracts and an explicit version-gated capability registry. Preserve separate evidence planes and unknown provenance; discovery performs no source reads, plugin imports or network calls. Production connectors and checkpoint persistence remain pending.

## 0.5.1

### Patch Changes

- Keep doctor readiness checks limited to bank metadata, explicitly report unverified fixture/oracle contents, and block unsupported filesystem capabilities without mutation. Preserve full bank integrity verification for audits and evaluation, and reject unsafe or malformed bank files with bounded reads.

## 0.5.0

### Minor Changes

- 5508681: Add explicit project configuration to evaluate. Validate requested tasks, fixtures
  and oracle bundles against the configured bank, narrow harness/runtime/resource
  scope, record private effective settings and invalidate changed configuration.
  Unsupported profiles block execution. Configuration stays opt-in and existing
  invocation syntax remains compatible.
  Freeze evaluation inputs before runtime version queries, and stop dispatch when
  those queries or later work change frozen input contents or configured file modes.

## 0.4.0

### Minor Changes

- d299507: Add explicit offline fixture-command execution to paired evaluations. Executors
  receive only their fresh fixture and selected read-only harness; a separate
  offline grader evaluates bounded artifact copies with a pristine oracle bundle.
  Unsupported sandbox hosts block without host fallback. Export artifact allowlists
  with task-bank manifests. This mode makes no model calls or automatic promotions.
- 9c19d25: Add offline doctor checks, strict project configuration and preview-first init.
  Creation is explicit, atomic and exclusive; diagnostics omit private file contents
  and keep authentication and full isolation unverified. Configuration consumption
  is currently limited to doctor/init and does not authorize runtime execution.

### Patch Changes

- 0544449: Add opt-in offline sandbox regrading for task-bank controls. Extract bounded
  allowlisted artifacts into fresh read-only inputs, verify pristine oracle copies,
  and reject malformed or inconsistent typed grades. Unsupported sandbox hosts
  report blocked without host fallback. Include calculator source in its artifact
  contract; behavioral promotion remains disabled.
- bfef7d9: Reject writable sandbox workspaces that overlap read-only inputs. Bound combined
  stdout/stderr capture, clean up workers when selector setup fails, and count
  preflight work against the execution deadline. Share one wall-clock budget across
  offline grader audits and preserve completed checks on timeout or exhaustion.
- 9e8542f: Require zero production-source clones in jscpd working-tree and staged pre-commit
  checks. Remove the clone baseline and share telemetry identity registration logic
  without changing idempotence, conflict detection or retention behavior.

## 0.3.0

### Minor Changes

- d83b6a1: Add bounded development-only mechanism/search APIs, explicit skill/adapter/retrieval evidence contracts, private journaled adoption and guarded manual rollback, matched benchmark publication checks, offline package CI and isolated retrieval-policy simulators. Keep unsupported runtime integration, calibration and live certification explicit; no paid execution, automatic rollout or training is enabled.
- 248d3a9: Add frozen paired experiment plans, conservative root-level analysis, task-bank integrity and split checks, single-use certification access, bounded artifact extraction and an optional offline sandbox capability probe. Keep behavioral changes recommendation-only while isolation and independent certification prerequisites are unverified.
- a2a3d3a: Ship a reproducible synthetic starter task bank, objective grader falsification controls, safe descriptive manifest export and typed grading envelopes. Freeze complete declared grader bundles, preserve graded outcome provenance and include runtime fixture assets in wheel/archive installs. Keep task independence and live certification explicitly unverified.
- 1804bc2: Add typed v2 metadata envelopes, bounded native call/result correlation, a private idempotent ledger and outcome provenance APIs. Preserve successful evaluation controls without treating completion as correctness. Apply accepted capture limits after project filtering and report scan exhaustion.

### Patch Changes

- 82fe77e: Detect repeated observations across explicitly read-only calls with bounded state-aware caches. Persist watch attempts atomically, limit transient retries, preserve interrupted/corrupt checkpoints, and return truthful CLI exit codes for incomplete and blocked runs.
- 10c1f55: Explain that diagnosis requires a fresh output path while preserving existing-output refusal and exit code 2.

## 0.2.0

### Minor Changes

- Install the CLI and both agent skills from verified GitHub release assets without cloning. Run automatic diagnosis and improvement through one CLI, and manage versions and release changelogs with Changesets.

## 0.1.0 — 2026-10-31

- Project-owned harness evaluation and self-improvement skills.
- Dependency-free trace mining, offline diagnosis, and paired Codex evaluation.
- Optional local classifier benchmark with root-separated splits and abstention.
- Research-to-criterion references and open-source contribution guidance.

Experimental release: synthetic smoke checks do not establish
production diagnostic accuracy or cross-project reliability.
