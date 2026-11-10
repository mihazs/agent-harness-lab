# Quality-constrained resource decisions

`agent_harness_lab.evaluation.resource_decision.decide(contract, pairs, quality_status)` compares
candidate and baseline cost or latency only under a frozen resource contract. It is opt-in,
recommendation-only (`automatic_promotion` is always `false`) and does not change existing
analysis outputs. Synthetic or caller-supplied paired records only; it makes no model calls.

## Contract (`resource-noninferiority-v1`)

`schema_version`, `profile`, `alpha` (resource family level), `min_roots`, and `endpoints`.
Each endpoint declares `name`, `kind` (`elapsed_wall_seconds` or `measured_tokens`; currency is
refused until frozen dated rates are supported), `unit`, `scope` (`all_assigned` only),
`required`, `horizon` (the operational cap) and `max_increase` (the practical margin for
candidate minus baseline). Thresholds are declared by the experimenter, never chosen here.
The report carries `contract_sha256`, so any change yields a new identity.

## Estimand and decision

- Each endpoint is the capped all-assigned mean difference, averaged within each independent
  root and bounded with the weighted-root Hoeffding bound (`alpha` split across required endpoints).
  A success-only filter is not offered, so slow failures stay in the estimate.
- An `agent_timeout` at or above the horizon counts as the capped value and is counted in
  `capped_observations`. A timeout below the horizon is censored and therefore unknown.
- Infrastructure interruption, cancellation, a missing or invalid measurement, or no pairs make
  the endpoint `unknown`; unknown is never imputed as zero.
- Endpoint status: `improvement` (upper bound below zero), `noninferior` (upper bound within
  `max_increase`), `regression` (lower bound above `max_increase`), else `insufficient_precision`.
- Decision order: quality must be `supported` (the status of the frozen quality analysis);
  anything else gives `quality_not_verified` and no efficiency claim, though endpoint results
  remain visible for review. Then any unknown required endpoint is `blocked`, any regression is
  `regression`, imprecision is `inconclusive`, and only all-required `improvement` is
  `improvement` (`efficiency_improvement: true`); otherwise `noninferior`.
- Advisory endpoints are reported but never affect the decision; absent data shows `unknown`.

## Cache-aware contract (`resource-noninferiority-v2`)

Version 2 adds `cache_policy` to the frozen contract, either `uncached_only` or
`provider_cache_allowed`. Each arm supplies `provenance` with `execution` equal to
`new_request`, `cache_state` equal to `uncached` or `provider_cache_used`, and
`measurement_scope_complete: true`. A local replay, unknown cache state, missing provenance,
or incomplete scope makes the endpoint unknown. `uncached_only` rejects provider cache
hits; `provider_cache_allowed` allows either declared state when both arms of each pair match.
Asymmetric cache states block the endpoint. The policy is part
of the contract digest. Actual measured tokens must include all relevant billed usage; cached
tokens are not inferred to be free. Quality still gates the final decision.
Version 2 accepts seconds (`s`) for elapsed wall time and `tokens` for measured tokens;
contradictory units are rejected.

These fields are caller attestations, not independently verified cache state or custody. A
caller must only mark the scope complete when the measurements include the declared target
execution scope, including applicable retries and charges. Replay and regrading are not new
target executions. This API does not collect cache-read/write categories, verify cache balance,
or prevent a caller from supplying false provenance. Version 1 remains available with its
original semantics and does not check provenance.

Every v2 result and endpoint labels provenance as `caller_attested_unverified`, quality as
`caller_supplied_unverified`, and its decision or inference basis as `conditional_on_caller_inputs`.
Favorable results say `conditional_improvement` and set
`conditional_efficiency_improvement: true`; `efficiency_improvement` remains false. These
labels also qualify noninferiority and regression decisions. They do not authenticate the
measurements or establish quality. Automatic promotion remains
disabled.

## Frozen timing boundary (`resource-noninferiority-v3`)

Version 3 adds a frozen `timing_boundary` object with interval
`target_subprocess_launch_through_collection` and seven Boolean fields: `setup`,
`queueing`, `retries`, `tool_calls`, `user_wait`, `extraction`, and `grading`. Each field
states whether that component is included in the declared elapsed measurement. Every arm of
an elapsed endpoint must attest to the identical boundary in its `provenance`; a missing,
incomplete, or different boundary makes that endpoint unknown. The boundary appears in the
endpoint report and contributes to the contract digest. Version 2 remains unchanged.

The interval declares timing from immediately before target subprocess launch through collection
of its result, including termination cleanup when applicable. The boundary is caller-attested,
not independently measured or verified. It does not guarantee retry accounting or convert a
runner's execution time into end-to-end user latency. Its flags do not establish that a component actually occurred.
Only include an elapsed observation when its measurement and complete scope are known to match
the frozen declaration. Version 3 retains version 2's conditional decisions and quality gate.

Not included yet: ratios and cost-per-success (reported as `not_computed`), success-conditional
latency, currency, automatic provenance capture and report integration.

## Responses controller usage

The Responses controller retains a valid provider-reported `input_tokens_details.cached_tokens`
as `cached_input_tokens` in the individual request's `provider_reported_usage`. This count is
included in input tokens; it is not an additional token total. A missing or null detail object
stays absent, and an invalid or inconsistent count is recorded as unknown (`null`). This is a provider
observation, not independent verification of cache state, complete billing, or a resource
comparison. Cache-write charges and unreported retry usage remain unknown.

`agent_harness_lab.evaluation.responses.summarize_provider_usage(report)` optionally summarizes
the saved controller report as `controller-provider-usage-summary-v1`. Per-category totals are
available only when every request attempt has that category; known subtotals and coverage
remain visible otherwise. Missing or failed responses are unknown, including when no requests
were attempted. Cached input is already included in input tokens and must not be added again.
Cache-write tokens and billing completeness remain unknown. This descriptive summary does not
authenticate provider data or produce a resource decision, and does not change controller reports.
The summary is private, labels publication as disallowed, and refuses `public=True`.

`Client.usage` is a read-only snapshot: modifying its list or rows does not change later
controller reports, and assigning to the attribute raises `AttributeError`. Callers that
previously mutated this view must instead keep their own data. This prevents accidental
aliasing; it does not authenticate provider usage or isolate code in the same process.

`agent_harness_lab.evaluation.trial_usage.current_trial_usage()` exposes a private, live-only
view of request usage for the currently admitted fixed comparison trial. It binds request
ordinals to the active scheduled trial, captures validated provider usage before checkpoint
callbacks, and releases it only after comparison custody accepts the request. Failed or
unobserved attempts remain unknown. The view is unavailable outside the active trial, refuses
public export, and does not expose usage from a previous trial. It is not a durable receipt:
restart and resume cannot reconstruct its contents. Provider data and trial identity remain
unverified outside this live controller context, and this view does not authorize promotion or
establish billing completeness. It is not a security boundary against code in the same process
and is not automatically used in resource decisions.
