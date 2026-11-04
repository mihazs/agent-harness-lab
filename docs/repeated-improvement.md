# Fixed repeated-bank improvement

`ahl improve --experiment-request REQUEST.json --agent responses` optionally evaluates
one frozen candidate against an explicitly selected development/regression bank.
It executes fresh controller requests and isolated workers, seals private history,
produces an existing decision receipt, verifies it, and reports the resulting analysis.
The project harness stays unchanged. A supported analysis recommends human review;
it does not grant adoption or certification authority.

Without this option, the existing descriptive workflow is unchanged. Sequential
experiments remain unsupported by `improve`. This option does not implement an
untouched certification issuer, scientific root qualification, or verified model identity.

## Prepare the explicit inputs

Use a separate, bounded bank directory outside the project and every proposer,
harness and candidate input view. The bank contains an exported manifest, frozen
Python artifact grader bundles, task fixtures, and broken/partial/complete grader
controls. Every selected task needs explicit `root`, `family`, `artifacts`, and
`development` or `regression` split. `skills` and `harness_paths` must be empty.
Do not include an existing experiment or protected-access declaration. Grader
controls run in the existing offline sandbox before trial execution. A requested
permission profile must qualify controls, execution and graders; a violation or
unknown result withholds the review recommendation. The requested profile is pinned
before dispatch and requires matching qualified kernel facts, including grader
self-tests. A missing monitor cannot become an accepted unmonitored run.

The request is strict JSON with these fields:

| Field | Meaning |
| --- | --- |
| `schema_version` | Integer `1` |
| `profile` | `ahl-improve-fixed-bank-v1` |
| `manifest` | Explicit manifest file path |
| `manifest_sha256` | SHA-256 of the exact manifest bytes |
| `bank_sha256` | Existing integrity-tree digest of its complete directory |
| `experiment_id` | Bounded experiment name |
| `repeats`, `seed`, `max_trials`, `min_roots` | Existing fixed ExperimentSpec parameters |
| `effect_margin`, `family_margin`, `alpha` | Frozen decision margins and error level |
| `family_weights` | Object mapping every selected family to its positive frozen weight |
| `precision_half_width` | Explicit analytic target, or JSON `null` |
| `operator_review` | Bounded operator declaration; retained as **unverified** |
| `cache_policy` | `fresh_controller_calls_provider_cache_unknown` |

The repository task `task input:digest INPUT_DIR=/explicit/bank` computes the tree digest. Hash the raw
manifest bytes separately. The request must pin both; it is not a bank-curation or
oracle-validity certificate.

Supply the existing explicit inference settings, delegated cgroup, pinned runtime,
private disk and whole-run timeout options. Keep the original bank authority tree, exported bank, request, settings, credential
files, source caches and history outside every model input, including the complete
mounted runtime image. Requests overlapping that image are refused before inference. The controller freezes the bank and
request before proposer inference. An explicit infeasible precision target blocks
before sandbox, credential or model access. The request also refuses shared call/tool caps below the best-case minimum needed
for proposer publication and every fresh worker trial. Actual consumption may exhaust
the budget sooner; time feasibility remains unknown. This analytic check is not power or a
prediction of provider cost or completion time.

The proposer receives its existing minimal metadata and baseline views. It cannot
see the private bank, request, history or settings. Once the candidate is frozen,
the controller creates a **new** ExperimentSpec with actual baseline, candidate,
oracle and runtime hashes, retaining the original request hash. It does not rewrite
a previously frozen execution plan.

## Read the result

Private output contains `fixed-request.json`, the copied `fixed-bank`, and
`private-history`. History schema 6 records fresh Responses comparison custody.
Each scheduled trial binds its actual pre-launch seeded fixture and subsequent captured-state chain, frozen harness, fresh
protocol-completed requests, registered kernel dispatches and artifact-grade
handoff. Legacy Spec byte hashes remain unchanged. Separate per-arm bundle pins capture
content and file/directory modes at actual binding before controls. Trials and
actual read-only input observations must match those original pins; changed
modes cannot become a new per-trial admission identity.

Source and permission consumers share the existing kernel fact producer;
there is no second monitor. Saved result JSON cannot create a live completion.

The workflow reports the verified decision and analysis. A conditionally supported
analysis produces `recommendation: review_candidate`; other complete analyses produce
`candidate_not_supported`. The overall status remains `inconclusive` because
certification prerequisites are unverified. Missing artifacts, unknown observations,
unresolved requests, incomplete custody or expired budgets cannot produce a favorable
decision. No ambiguous request is automatically retried, and cold execution resume is
unsupported. Tampered sealed inputs, results, custody or receipt bytes fail verification.

Before dispatch, the private store records the realized trial schedule. The existing
inference checkpoint binds the reserved cumulative budget to the issuing process's
active role, actual trial and frozen comparison schedule. Interrupted checkpoint writes
prevent dispatch and retain the spent reservation. These records are diagnostic data;
they cannot restore a live completion handle, authorize retries or count local replay
as fresh execution. Legacy histories without this additional schedule record remain
readable. Cold resume and provider-side reconciliation remain unsupported.

`experiment-history show` reports `planned_trials` from the authenticated frozen
fixed plan. `sealed_result_trials` counts result rows only after the existing seal,
exact schedule and evidence checks pass (`execution_coverage: sealed_fixed_schedule`).
For running or incomplete histories, coverage is `unknown` and the sealed count is
null, even if partial result files exist. This count describes recorded trial rows,
not provider calls, successful tasks or independent roots. Oracle quarantine still
invalidates a sealed comparison; coverage does not grant decision authority.
Sequential histories retain their separate prefix coverage report.

Fresh controller calls and worker execution do not authenticate the served model or
prove uncached provider computation. Native agent identity, effective provider
configuration, scientific independence, oracle review and untouched certification
remain unverified. Repeats are grouped within declared roots; assertions and worker
counts do not create independent roots. All records and receipts remain private,
including results from synthetic banks, and automatic promotion remains false.

## Shared construction store

Pass `--evaluation-store PATH` with `--experiment-request` to use the same private
HistoryStore for the owned proposer stage and the repeated comparison. Without the
selector, the existing per-output `private-history` location remains in use.
The selected store must be outside the project, fixed bank, bank owner, runtime,
settings, credentials and output. Selection is checked before creating store
metadata, and the request digest remains bound through proposer execution.
Configured trial limits cover the proposer and all frozen repeated trials; an
insufficient limit blocks execution without reducing repeats or changing margins.

The fixed bank is not supplied to the proposer and does not become its scientific
parent merely because the controller selected it. Recording construction and
comparison in one store does not establish independent roots, certification or
automatic adoption.

## Verification

`task test:workflow:repeated` runs native unittest admission and custody cases.
`task test:workflow:repeated:kernel` uses the existing explicitly delegated Linux
profile for public-CLI synthetic transport tests with actual isolated workers.
It makes no real provider calls and authenticates no real model or native agent.
`task test:workflow:repeated-access` checks the shared-store selection boundaries;
`task test:workflow:repeated-access:kernel` exercises their actual public controller
composition under the same delegated profile.
