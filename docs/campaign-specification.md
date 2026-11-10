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

`CampaignStore.create(spec, project_root)` creates a private planned record
under an owner-only store outside the project, with an opaque campaign ID.
`CampaignStore.inspect(id)` validates the saved specification against the
creation event and returns a private summary. Both operations are offline;
their status fields explicitly say that budget enforcement, execution, review,
evaluation and deployment have not occurred. Store locking excludes a second
controller for that store. The project/store separation check observes current
paths; it does not protect against concurrent renames of their parent directories.
Use a store whose ancestry remains controlled for the duration of creation. Neither operation verifies caller-supplied project
identity hashes. A failed or incomplete creation is not an executable campaign.

The offline CLI exposes the same planned-only operations:

```text
ahl campaign --store PRIVATE_STORE create --spec PRIVATE_SPEC.json --project-root PROJECT
ahl campaign --store PRIVATE_STORE inspect --campaign CAMPAIGN_ID
```

Both commands print private JSON. Creation requires a complete v1 specification;
the specification and output contain private identities. Inspecting does not
start or resume work. Exit 0 means the requested offline action completed; 2
means invalid or unavailable input, and 3 means the store lock is busy.
