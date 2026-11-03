# Owned development variants

[Managed diagnostic access](protected-diagnostic-access.md) · [Controller history](oracle-history.md)

An explicit `derive-variants` operation now connects the existing controlled author
to the same private access ledger. It observes which scientific input bytes the
controller made available, then records the actual published child assets. The
relation is **may-derived**: availability does not establish model reads, semantic
necessity, independent roots, correctness or authenticated curation.

This is a new opt-in lane. Ordinary `improve` does not consume prior task data
through it. Raw `construction.author` calls without an owned handoff remain
unmanaged; the original raw-path freshness assertion still fails. The new command
does not fix or certify every controlled-author path. Missing, unmanaged and
cross-store ancestry remains unknown, and true certification remains unavailable.

## Explicit source and execution

The initial source must be an actual sealed managed run in this configured
`HistoryStore`, with its retired access receipt, frozen manifest, oracle and bank
bindings. Original fixture, baseline and candidate files must still match the
content pins that the managed source recorded. Missing earlier evidence is not
reconstructed. Prior bank/oracle applicability is not approved again; these are
exposed development inputs, including when their historical authority later changes.

```sh
ahl experiment-history --store PRIVATE_HISTORY derive-variants \
  --source-run SOURCE_RUN --task SOURCE_TASK \
  --output NEW_PRIVATE_VARIANTS --inference-settings EXPLICIT_SETTINGS.json \
  --cgroup-root DELEGATION --image-root PINNED_IMAGE \
  --image-sha256 SOURCE_IMAGE_SHA256 --disk-bytes DISK_CAP
```

`--task` may repeat to select source tasks; omit it to select all supported source
tasks. The runtime image must match the source runtime. Existing Responses settings,
request/tool quotas, offline worker limits and explicit credential handling apply.
No native agent is launched. Running this command with real settings explicitly
uses that provider; native tests use fake transport and synthetic keys only.

The controller supplies only the selected scientific fixtures/oracles and a source
manifest, plus the baseline as a separate input. It never gives the author the
candidate. This variant author is explicitly told it is creating exposed
development data, rather than independent evaluation. The existing `run_role` and
`WorkingState` perform execution, capture and publication; no second runner exists.

The result contains a genuine `derivation_id` and `state: complete`, with
`qualification: unverified`, `scientific_independence: unverified` and publication/
automatic-promotion disabled. This means the construction transaction completed;
it does not mean the generated oracle passed its controls. No TaskSpec, reviewed
bank, comparison result or certification is created automatically.
Generated task roles remain exposed development/regression; an author's protected
split label is declassified before publication.

Completed owned outputs can be the source of another operation:

```sh
ahl experiment-history --store PRIVATE_HISTORY derive-variants \
  --source-derivation DERIVATION_ID --output NEW_PRIVATE_GRANDCHILD \
  --inference-settings EXPLICIT_SETTINGS.json --cgroup-root DELEGATION \
  --image-root PINNED_IMAGE --image-sha256 SOURCE_IMAGE_SHA256 --disk-bytes DISK_CAP
ahl experiment-history --store PRIVATE_HISTORY derivation-status --derivation-id DERIVATION_ID
```

Transitive sources reopen the actual owned snapshots and bindings. A copied JSON
receipt, arbitrary run ID, copied handle or renamed output cannot supply custody.
Published variants still require separately authored plans, audited graders and
the existing reviewed bank-admission workflow where applicable.

## Exposure transaction and identity

The existing CertificationLedger lock owns a schema-3 transaction. Versions 1 and 2
retain their previous interpretation; legacy standalone receipts cannot become
owned derivations. The controller records pending construction before preparation,
pins the actual input view/baseline, settings and full evaluator engine, and verifies
them before author dispatch and publication. Current handoff snapshots cover file
bytes/modes and root/directory modes. It does not invent missing original directory
mode evidence in older source histories.

Initial authored manifests are validated and normalized inside the role snapshot
before its single publication, preserving modes and the shared byte/deadline bounds.
The producing role registers its output snapshot once. Asset capture must match
that snapshot's bytes and file modes, including changes restored before readback.
Only the controller's manifest rebasing and private root mode may change afterward;
the rebased snapshot is materialized in a new private directory without rewriting
the producer tree. Publication copies that expected snapshot rather than recapturing
mutable files. New output bindings are version 2; version-1 bindings remain readable.
These checks do not make the host filesystem atomic against external writers.

Before returning or exporting child data, the transaction records computed prompt,
fixture and oracle identities, frozen source/engine/settings/output bindings and
the may-derived parent relation. Child/grandchild assets cannot receive fresh
managed access in that store, even when all bytes, names and oracle revisions were
changed through this owned operation. Outer-path aliases, task/root relabeling or
removing the descriptive marker does not erase the controller's content binding.

Ordinary descriptive development comparisons of derived data remain available.
Their histories freeze any actual known derivation links; caller-authored marker
JSON does not supply that binding. Old completed source histories are not rewritten.
Existing managed-retired asset restrictions remain unchanged.

The relationship does not union scientific roots. Shared baseline instructions,
methodology and runtime configuration are pinned controls, not universal scientific
parents. Only the selected scientific view supplies parent assets. A child can
have known ancestry without providing any verified independence claim.

## Pending work, capacity and failure

Managed reservation and admission use the same lock and refuse while construction
is pending or unresolved. This prevents a race before new child identities become
known; ordinary development comparisons remain possible. No ledger lock is held
across worker/provider calls. Before dispatch, the 1 MiB ledger ceiling reserves
worst-case completion metadata, including up to the existing 3,000 asset bound.
Unrelated observations cannot spend that terminal space. These are storage bounds,
not experiment sample quotas or power claims.

One finite parent deadline covers source checks, copying, authoring, output
registration, publication and final validation. The default is 60 seconds and the
existing maximum is 300 seconds. Working-state file/byte limits remain unchanged;
select a smaller explicit source set if it exceeds that supported view.

An interruption or invalid/missing capture never refunds exposure. The transaction
remains pending or becomes unresolved, and further managed freshness requires
intervention. A pending state can mean active or interrupted work; there is no
stale-age inference or automatic reset/recovery command. Ordinary development is
still available. Moving to another store does not establish untouched data.
If output registration completed before an export/deadline failure, status can
truthfully retain completed private construction without claiming external export
success. Retirement never proves that a particular model read the source.

## Remaining scope and executable evidence

This does not intercept arbitrary host access, third-party writers, raw author calls,
manual semantic rewrites or cross-store copies. Direct scripted-reply delivery (#123),
separate rubric/policy handoffs and discovered-failure feedback are not wired into
this lane yet. Full #8, scientific qualification and native/runtime identity remain
open. Generated graders remain untrusted until independently audited.

Run `task test:evaluation:derivation`; the canonical installed target forwards
`PYTHON`, `LAYOUT_DIR` and `EXPECTED_PACKAGE_ROOT` and checks actual application
imports. Tests use public synthetic data, actual Python worker subprocesses, fake
transport and cooperative attestations; they do not qualify containment or models.

The exact original unmanaged falsifier is retained separately:

```sh
python3 -m unittest discover -s tools/tests -p derivation_raw_assessment.py -v
```

It intentionally exits 1: the raw path still obtains an unqualified fresh diagnostic
reservation after generating new bytes. The normal suite executes a bounded
assessment of that remaining result rather than counting it as fixed. The owned
CLI regression separately verifies tracked children are refused fresh managed access.
