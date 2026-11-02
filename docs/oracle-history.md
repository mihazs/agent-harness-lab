# Controller comparison history and oracle quarantine

Version 0.45 adds `ahl experiment-history`. It records actual complete offline
comparisons under an explicit private controller store. An operator can then
quarantine a defective grader bundle, invalidating all recorded comparisons that
depended on it, including plans containing different task subsets.

Historical reports retain their original bytes and oracle attribution. `current`
means registered and not quarantined in this store. It does not mean the oracle
was independently audited, roots are independent, runtime provenance is
authenticated, or a harness improved. All summaries retain `oracle_review` and
`independence` as `unverified`; automatic promotion and publication remain false.

## Record a fresh comparison

Prepare an exported manifest with a frozen `experiment` plan. Its `grader_hash`
must match `task experiment:oracle MANIFEST=/path/to/manifest.json`. Its
`runtime_hash` must equal the selected pinned image SHA-256. Task/root/family
declarations and both harness hashes must match the selected inputs.

```sh
ahl experiment-history --store /path/to/private-history run \
  --manifest /path/to/export/manifest.json \
  --baseline /path/to/baseline --candidate /path/to/candidate \
  --cgroup-root /path/to/delegation \
  --image-root /path/to/runtime --image-sha256 IMAGE_SHA256 \
  --disk-bytes 67108864 --budget-seconds 60
```

This command uses the existing offline runner and fresh executor/grader workers;
it has no model, native-agent or network fallback. Full cgroup, pinned-image and
private-disk profiles are required. There is no result-import option. A supplied
successful JSON receipt cannot create a recorded comparison.

The controller generates an opaque run ID, retains the exact source manifest and
oracle bundles, and executes into a new private case directory. It requires the
complete scheduled baseline/candidate trial set, unchanged inputs, actual
executor completion, typed grader output and matching worker attestations.
Failed task grades remain failures; missing/invalid grades and incomplete arms
cannot produce sealed eligible evidence. A baseline that already passes is
retained truthfully rather than forced into a reproduced-failure label.

The seal binds exact result bytes and the execution tree, including observed
artifacts/traces. Metadata is synced before the completion event. Interrupted or
oversized evidence remains incomplete; surviving files alone are never adopted.
A fresh retry executes both arms again under a new run ID. Frozen execution
evidence is bounded to 64 MiB/4,096 files and the store to 256 case directories,
including oracle registrations and incomplete cases. A single parent deadline
covers execution and evidence sealing. These are resource bounds, not statistical
sample-size or power claims. This does not provide a total controller-disk quota.

The store must be separate from selected worker inputs. It uses owner-only
directories, strict regular-file reads, immutable event chains and the existing
file-tree digest semantics; empty directories/root-directory modes are not
included in those digests. Source inputs must remain stable through completion.
Historical reads use the retained evidence without reopening original inputs.

## Inspect and quarantine a grader dependency

```sh
ahl experiment-history --store /path/to/private-history show --run RUN_SHA256
ahl experiment-history --store /path/to/private-history analyze --run RUN_SHA256
ahl experiment-history --store /path/to/private-history oracle --bundle BUNDLE_SHA256
```

The bundle command returns the exact current event identity for a quarantine
record. Version 1 requires exactly these fields:

| Field | Meaning |
| --- | --- |
| `schema_version` | Integer `1` |
| `bundle_sha256` | An observed complete bundle identity |
| `expected_event_sha256` | Exact current oracle event identity |
| `operator` | Exactly `identity`, `source`, `provenance`, `basis`; source is `operator_attestation` |
| `evidence_refs` | Nonempty bounded list of `{ref, sha256}` entries |
| `reason` | Nonempty bounded single-line explanation |

```sh
ahl experiment-history --store /path/to/private-history quarantine \
  --record /path/to/private-quarantine.json
```

Quarantine is a negative operator decision, not positive grader approval or
authenticated independent review. It is monotone; stale decisions reject and
there is no unquarantine operation. Shared bundle dependencies invalidate all
affected recorded comparisons, even when their aggregate oracle hashes differ.
Future executions using that bundle reject before dispatch. A decision committed
during a run invalidates its eventual comparison as well. Old reports are never
rewritten and failed/interrupted reruns never restore old eligibility.

## Analysis and correction boundaries

Guarded analysis reads sealed controller evidence and current dependency state.
Invalidated or incomplete runs cannot yield supported current evidence. The
legacy `ahl experiment --spec ... --records ...` command remains mathematical
diagnostics with `oracle_validity=unverified`; supplied matching old hashes cannot
establish current validity. Certification promotion also requires current oracle
history in addition to its other existing prerequisites.

A changed bundle needs a new aggregate oracle identity, a new frozen plan and a
fresh complete both-arm execution. Merely regrading old artifacts or supplying
one arm is insufficient. New bundles remain unaudited. Positive correction
audits, independent artifact review, approved immutable bank replacement and
native provenance binding remain subsequent work in issue #8. This command does
not relax regression-bank duplicate/admission checks or approve a correction.

`run` returns 0 only for a sealed, non-quarantined comparison; it does not imply
improvement. Incomplete execution returns 1, invalidated/busy state returns 3,
and invalid input returns 2. `analyze` remains diagnostic and returns 3. Read and
quarantine operations return 0 when they succeed. Keep all private inputs, run
IDs, traces, derived reports and aggregates private.

Run `task test:evaluation:history` for native contracts, and
`task test:sandbox:kernel:history` for the separately selected real pinned-kernel
canary using reproducible synthetic fixtures. Mocked attestations test controller
acceptance; they are not containment or authentication evidence.
