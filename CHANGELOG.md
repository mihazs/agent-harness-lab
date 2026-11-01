# Changelog

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
