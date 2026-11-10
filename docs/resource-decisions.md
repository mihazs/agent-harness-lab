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

## Frozen token scope (`resource-noninferiority-v4`)

Version 4 retains version 3's timing and cache policies. Each `measured_tokens` endpoint also
declares `token_category` (`input_tokens`, `output_tokens`, `total_tokens`, or
`cached_input_tokens`) and `token_scope: controller_provider_responses`. This scope counts only
the declared category reported for the target controller's provider requests; it does not
represent all task costs, tool charges, cache-write charges, or currency. Cached input is a
subset of input tokens and must not be added to the input count. Category, scope, cache policy,
and timing boundary are part of the contract digest.

For each token endpoint and arm, `measurement_provenance[endpoint_name]` supplies the matching
`token_category`, `token_scope`, `request_attempts`, `known_requests`, and `unknown_requests`.
The endpoint is unknown unless at least one request was attempted and all attempts have that
category. The existing complete-scope and fresh-execution declarations still apply; for this
scope, completeness means all target-controller provider attempts in the declared measurement,
not complete billing. Missing categories or requests are never assumed zero. These declarations,
including the cache state, are caller attestations and are not independently authenticated.
For version 4, `uncached` attests that every included request is uncached, and
`provider_cache_used` attests that every included request read at least one cached input token;
cache writes alone do not satisfy that state. A mixture or
unknown state must remain unknown; it cannot be represented by either value.
Token observations must be nonnegative integers. For `cached_input_tokens`, a positive count
contradicts `uncached`; under `provider_cache_used`, the count cannot be smaller than the
number of included requests. Contradictory observations remain unknown.
Version 4 accepts at most 64 endpoints, 1,000 requests per token observation, and 10^15 tokens
per observation; values outside these bounds are refused or remain unknown.
When several categories are supplied for the same arm and scope, complete request counts must
match, cached input cannot exceed input, and total must equal input plus output when all three
are present. A missing category is not inferred from the others.
Results retain the conditional and private-data limitations described above. No automatic bridge
from live or completed trial summaries to a resource decision is provided. Version 4 results are
private and explicitly marked as disallowed for publication.

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

`agent_harness_lab.evaluation.trial_usage.summarize_current_trial_usage()` aggregates the
currently visible provider-reported categories for that active trial without exposing raw
request rows. It reports request coverage and known subtotals, but all trial totals remain
unknown: more requests may occur before the trial completes. Failed or missing requests and
unreported cache categories stay unknown. The summary is private, live-only, and does not
establish quality, billing completeness, or eligibility for a resource decision.

`agent_harness_lab.evaluation.trial_usage.latest_completed_trial_usage()` returns a private,
live-only summary of the latest trial after comparison custody accepts its worker and grade
observations. It is unavailable during another active trial or after the comparison closes.
Request binding coverage is reported separately from measurement availability; missing usage
remains unknown, and all final totals remain unknown. This sidecar is not part of the existing
durable custody receipt and cannot be recovered after restart. Provider data remain unverified;
the sidecar does not establish cost, billing completeness, a quality decision, or promotion.
It is not a security boundary against other code in the same process.

`agent_harness_lab.evaluation.resource_collection.UsageCollector` can be attached to an
owned live fixed comparison before its first trial. It snapshots the v4 resource contract,
plan digest, and schedule digest, then collects the private completed-trial summaries in
schedule order. Its private report requires every planned trial and a result accepted by
`Comparison.finish`; incomplete or mismatched collection is refused. The resource contract
is bound by the live collector before the first trial, not embedded in the experiment plan.
The collector is opt-in, process-local, non-durable, and is not a same-process security
boundary. It does not inject summaries into worker or provider inputs, authorize promotion,
establish quality or billing completeness, or produce a resource decision. Provider usage
remains unverified and all final totals remain unknown.
Ordinary observer exceptions invalidate collection without undoing the already accepted trial.
Interruptions such as `KeyboardInterrupt` propagate after the trial may have been committed;
they do not establish a complete collection or permit replaying that trial as a fresh request.

An experiment plan may optionally declare `resource_contract_sha256` using the v4 contract's
canonical digest. A collector refuses a different contract before dispatch and reports that
the plan contains the digest, not the contract bytes. Plans without the field keep their
existing serialization, digest, and trial identities. Adding a pin creates a new plan and new
trial identities; it cannot retrofit an existing run. The pin identifies a declaration only:
history does not preserve the contract bytes, and the plan does not itself collect resource
data, apply a resource gate, authenticate measurements, or authorize promotion.

`agent_harness_lab.evaluation.resource_decision.verify_plan_resource_contract(plan, contract)`
compares a supplied plan declaration and v4 contract, returning `matched`, `mismatch`, or
`unbound`. This private check can be used on a plan obtained from history, but it treats the
supplied plan and contract as unverified: it does not authenticate history, establish that
contract bytes were persisted, recover collection data, or compute a resource decision.
The live collector uses the same check when a plan includes a resource contract pin.
