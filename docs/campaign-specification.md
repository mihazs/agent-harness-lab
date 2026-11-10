# Campaign specification reference

`agent_harness_lab.campaigns.schema.CampaignSpec` is an offline, immutable
schema for a proposed local campaign. It does not create or run a campaign,
reserve a budget, launch a worker, review a candidate, or authorize deployment.

The v1 declaration contains SHA-256 identities for the project, baseline,
configuration, task bank, grader, runtime, authorization, evaluation plan, execution
profile and measurement policy;
a bounded strategy ID and version; sorted, unique instruction, skill or hook
surfaces; a trial cap, a time cap in seconds and a reserved final-trial count.
The retry policy is `manual_reconciliation_only` and the approval policy is
`explicit_review`. Limits are declarations and are not enforced by this schema.

`CampaignSpec.parse` accepts exactly the serialized fields, checks their types
and bounds, and returns an immutable value. `to_dict` returns a fresh JSON-ready
document. `digest` is SHA-256 over its canonical sorted, compact JSON encoding;
changing a declared input changes that identity. These hashes and declarations
are private and unverified. The specification sets publication and automatic
promotion to false. It does not establish a budget ledger, execution ownership,
recovery, quality, approval, certification, or application.
