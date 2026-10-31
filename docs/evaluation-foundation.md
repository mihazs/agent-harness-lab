# Evaluation foundation and remaining prerequisites

The current controller is recommendation-only for behavioral changes. Cooperative
smoke evaluation cannot authorize automatic adoption. Existing low-level manifests
remain descriptive. Missing/duplicate trials cannot yield a vacuous passing gate.

## Implemented contracts

- `ExperimentSpec` freezes task/root/family identities, repeats, seed, resource
  ceiling, effect/noninferiority margins, confidence, family weights and hashes.
  `schedule` produces replayable counterbalanced AB/BA pairs in fresh workspaces.
  Explicit repeated manifests must fit both their own and the caller's trial cap.
- `TrialRecord` analysis requires the exact planned trial set, executable graded
  binary outcomes and matching provenance. Infrastructure errors stay unknown.
  Root means retain correlated repeats together. Independent roots are an explicit
  assumption, not inferred from distinct strings or successful tests.
- The fixed analysis uses weighted-root Hoeffding intervals for bounded paired
  differences, with Bonferroni correction over the primary and family endpoints.
  This conservative method needs independent roots. It may be underpowered; a
  narrow interval or benefit is never manufactured from a tiny task set. It does
  not model drift or certify absence of rare harms. Unknown provider costs remain
  unknown. There is no optional stopping or task retry selection.
- The promotion policy separates descriptive results, incomplete/blocked evidence,
  and a recommendation after verified prerequisites. It never sets automatic
  promotion true. A recommendation API call is not an isolation attestation.
- `TaskSpec` and bank validation freeze fixture/grader bundles, provenance,
  partitions, safety and artifact boundaries. Root leakage is always rejected;
  optional family/project/time separation and copied-fixture checks are enforced.
  Unknown semantic independence/review blocks certification eligibility. Public
  tasks are explicitly not secret. Grader changes invalidate comparisons.
- A private controller-owned certification ledger reserves one final candidate and
  consumes access before exposing its bank. Search roles cannot request access.
  Concurrent access is single-use. API role checks require OS isolation; they are
  not a security boundary on their own.
- Artifact extraction rejects traversal, symlinks, hardlinks and special files,
  and enforces declared file/byte bounds after the worker stops.

## Offline sandbox and threat model

| Role | Visible assets | Forbidden assets |
| --- | --- | --- |
| Proposer | Authorized development evidence, harness | Certification, hidden oracle, host home |
| Evaluator author | Baseline, construction fixtures | Candidate/proposal, prior final feedback |
| Executor | Fixture and selected harness | Oracle, other runs, host credentials |
| Grader | Bounded extracted artifacts, pristine oracle | Project writes, network, credentials |
| Controller | Frozen inputs, access/budget ledgers | Unrelated production changes |

`SandboxBackend` is an opt-in offline Linux/bubblewrap primitive, never a silent
fallback. It builds a mount namespace from explicit system directories, one
workspace and role-allowed read-only inputs; clears inherited environment; and
requests separate network/process/user namespaces, capability removal, process
session cleanup and CPU/address-space/process/file/output/time limits. Input
integrity is rechecked. No host home/config/socket is mounted.

This is not a complete certified backend: aggregate disk/cgroup enforcement,
pinned full execution-image/dependency identity, a narrow inference broker,
full construction/execution/regrading integration and the complete adversarial
suite remain prerequisites for #7. Per-file rlimits are not aggregate disk quotas;
per-user process limits are not cgroup guarantees. Host/kernel compromise is out
of scope. `automatic_promotion=false` remains explicit even if an offline probe
succeeds. The local namespace probe fails on this managed host, so its live
containment-canary test is skipped and cannot be cited as security evidence.

[Bubblewrap's upstream documentation](https://github.com/containers/bubblewrap)
explains that application policy and the selected mounts determine the boundary;
the installed binary alone is not a security policy.

## Offline validation and limits

The tests cover exact coverage/provenance rejection, clustered repeats, critical
harms, a large synthetic benefit, conservative small-sample abstention, deterministic
scheduling, task/oracle integrity, split leakage, private single-use certification,
artifact escapes and unsupported capability behavior. A predeclared seeded null
check runs 100 synthetic experiments with 40 independent roots and correlated
within-root repeats; it permits at most 10 false promotions and observes zero.
Zero of 100 has a 95% Wilson upper bound of approximately 3.7%, not zero risk.
This is a falsification check under those assumptions, not general calibration.

The implementation stages for #7/#8/#9 remain tracked: a reviewed heterogeneous
executable task pack, grader falsification/mutants, manifest generation, full
sandbox integration, precision-planning simulations and independently graded
live certification are not claimed completed by these interface tests.

CLI: `ahl sandbox-check`, `ahl bank --manifest BANK.json --list`, and
`ahl experiment --spec SPEC.json --records RECORDS.json`. No command purchases
compute, downloads benchmarks or starts a background service.

## Reproducible starter bank

The bundled `starter-v1` pack adds seven small synthetic final-state tasks and
29 grader-control checks. It includes an executable calculator repair, preservation
checks, recovery/final-state handling, an instruction conflict, a skill-routing
decision probe, queue resumption and adversarial text as data. The routing probe
does not demonstrate actual skill activation. The pack is a deterministic starter
and falsification suite, not a representative coding benchmark or sufficient
certification sample. Related calculator cases share a root. All cases remain
`independence=unverified`; no protected certification/transfer population is
manufactured by relabeling these variants.

Broken, partial, complete and alternative-valid controls are provided for every
grader. The calculator also rejects a superficial output-only repair while grading
the function on additional inputs. Graders use a typed outcome envelope; invalid
references, malformed envelopes, contradictory exit codes and missing outcomes are
grader defects, not baseline task failures. Declared complete bundles (including
reference data and controls) are hashed before execution and checked afterward.
This detects changed declared inputs; clean-room regrading still requires #7.

`ahl bank --starter --list` validates and lists the pack. It exits 3 because task
independence remains unverified. `--audit --cooperative-controls` explicitly runs
the trusted synthetic grader controls with a minimal environment, without a model.
This is cooperative diagnostic execution, not sandbox evidence; audits refuse to
execute without that explicit mode until sandbox integration is available.
`--export NEW_DIRECTORY` writes a descriptive manifest with bounded fixtures,
grader bundles, control paths, task/root identities and an explicit no-promotion
flag. It never launches an agent. Source materialization is confined to declared
bounded `app/*.py` fixture paths and rejects traversal/symlinks.

The bank is reproduced by `scripts/build_synthetic_bank.py`, including under a
private umask. Its source programs are packaged as data and materialized only
inside disposable fixtures, avoiding installation-generated bytecode changing
frozen bank inputs. Both wheel and versioned archive include the complete pack.
These checks establish reproducibility and objective control behavior; fresh-model
performance, semantic independence and hidden-oracle isolation remain unexecuted.
