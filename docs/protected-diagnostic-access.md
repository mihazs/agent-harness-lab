# Managed protected-diagnostic access

[Bank qualification](bank-qualification.md) · [Controller history](oracle-history.md)

An explicit one-shot diagnostic comparison can now use the production controller's
`CertificationLedger` lifecycle. It protects known accesses within one configured
private `HistoryStore`. It does not qualify a bank, establish untouched data or
independent roots, or authorize certification, publication or adoption.

True `certification` experiment requests reject before worker dispatch because a
genuine qualification issuer is unavailable. Imported managed markers and caller
JSON cannot produce an owned invocation. The same claims also reject in the
improvement construction validator before grader self-tests. Existing default
improvement remains descriptive/inconclusive and never gains certification.

## Reserve, then run

Prepare an explicitly authorized descriptive paired manifest with frozen tasks,
fixtures, Python oracle bundles, baseline/candidate and runtime-image identities.
The existing plan owns its repeats, seed, margins, family weights and trial ceiling;
this lifecycle adds no sample quota. The bank remains unqualified.

```sh
ahl experiment-history --store PRIVATE_HISTORY reserve-access \
  --manifest PRIVATE_EXPORT/manifest.json --baseline BASELINE --candidate CANDIDATE
```

The result contains a controller-generated `reservation_id`, purpose
`protected-diagnostic-access-v1`, binding digest and `qualification: unverified`.
It contains no fixture body, prompt, reference or raw project path. This is an
access reservation, not a review receipt or certificate.

Use its ID with the actual offline comparison:

```sh
ahl experiment-history --store PRIVATE_HISTORY run \
  --manifest PRIVATE_EXPORT/manifest.json --baseline BASELINE --candidate CANDIDATE \
  --access-reservation RESERVATION_ID --cgroup-root DELEGATION \
  --image-root PINNED_IMAGE --image-sha256 APPROVED_IMAGE_SHA256 --disk-bytes DISK_CAP
```

Inspect reserved/retired status without inferring worker visibility:

```sh
ahl experiment-history --store PRIVATE_HISTORY access-status --reservation-id RESERVATION_ID
```

The controller reopens actual inputs and consumes the reservation before copying
oracles/preparing worker views. It records the genuine run ID and a hash-bound
private access receipt in that run. The runner additionally requires an opaque,
process-owned, single-use invocation and checks the live running case and inputs
before execution. A serialized receipt, legacy ledger, copied JSON or a direct
managed-marker call cannot replace that producer. Worker execution and grading
continue through the existing controller and resource profile.

Reservation and run operations retain finite budgets of at most 300 seconds;
`--budget-seconds` defaults to 60. Run preflight, admission, preparation and sealing
share the existing parent deadline. The reservation pins complete input file
bytes/modes, manifest/plan, baseline/candidate, oracle/runtime and controller engine.
Changed bindings reject; they are not silently recomputed as a different final
candidate. Managed preservation-cohort plans are unsupported in this first slice;
ordinary development/cohort comparisons remain available.

## Exposure and concurrency

Every new ordinary `HistoryStore.run` admission records the actual fixture/oracle
content identities as observed development exposure. Repeated ordinary comparisons
remain allowed. Those assets cannot subsequently obtain a fresh protected
reservation. With an existing reservation or retired managed asset, omitting the
reservation ID does not provide a bypass within that controller store.

Reservation, ordinary exposure and managed consumption use one private locked
atomic ledger transaction. If ordinary admission wins, a concurrent reservation
refuses the observed assets. If reservation wins, unmanaged admission refuses them.
Only one competing managed run can consume the reservation. A lock conflict fails
without an automatic retry or worker dispatch.

Reuse checks cover exact model-visible prompts, actual fixtures and complete oracle-bundle content, including
reference, control, rubric or scripted data when present in those captured trees.
They ignore outer paths, task/root labels and file modes for exposure identity, so
renaming a task, selecting a subset, changing candidate/plan or changing modes
cannot reset known retirement. File modes still participate in the frozen input
binding. Sharing a captured prompt, fixture or oracle conservatively couples managed access; it is
not a claim of scientific lineage. Byte changes or distinct IDs never prove new
semantic independence. Paraphrase/derivation authority remains separate work.

Consumption retires the assets before a worker may see the prepared data. A failed
capability check, interruption, callback failure or later invalid input does not
refund that access. A reservation may therefore remain retired without completed
trials. Retirement is not proof that a particular model read the data or violated
instructions. After feedback informs another proposal, the known retired assets
remain unavailable as a new managed diagnostic; they may remain exposed
unqualified development/regression controls through the supported development
scope. A managed-retired asset cannot be replayed by the default same-store run.

Completed historical views validate their frozen receipt against actual ledger
custody without rereading mutable original baseline/candidate paths. Original input
changes do not rewrite or upgrade prior evidence. Existing version-1 ledger calls
remain a standalone legacy API and cannot be upgraded into owned managed access.

The ledger is owner-only, refuses links/ambiguous JSON, and has a 1 MiB metadata
capacity, with terminal-consumption space reserved up front. Capacity exhaustion
blocks rather than evicting exposure history. This
is a storage bound, not a replacement for the plan's precision or trial budget.

## Limits and remaining work

This accounts for admissions observed by this configured controller. It does not
intercept arbitrary host reads, direct unmanaged runner use, different stores,
old untracked histories, transient within-tool accesses or external delivery.
Filesystem isolation remains #7's responsibility. The controller reads authorized
inputs to validate hashes; retirement precedes preparation/worker access, not
those trusted validation reads. Protected diagnostics do not claim that already
public or constructed fixtures were secret.

Scientific qualification remains unknown. A positive certification producer still
needs independently reviewed curation/lineage, qualified oracle evidence and
appropriate untouched exposure authority. Full #8 also retains semantic derivation
and exposure integration for paraphrases/replies/rubrics/failures, the complete
#123 behavior contract, and reviewed external-evaluation interchange. Default
`improve` feedback filtering does not establish non-exposure: held-out outcomes
still affect retry selection. This lifecycle does not recertify that legacy flow.

Run `task test:evaluation:protected-access`. Native tests use independently
synthetic files, actual executor/grader subprocesses and explicitly synthetic
cooperative attestations; they are not native containment or model qualification.
The canonical installed target forwards `PYTHON`, `LAYOUT_DIR` and
`EXPECTED_PACKAGE_ROOT` and checks actual application imports.
