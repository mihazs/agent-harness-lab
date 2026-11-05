# Controlled experiment APIs and operational recovery

These are tested implementation stages for #10–#17, not completed live studies.
They are standard-library, local-only APIs. Search, procedure compilation and
policy experiments are off unless explicitly invoked. Imports do not start an
agent, download a model, alter runtime permissions, install a skill or train a
model. Existing cooperative evaluation remains recommendation-only.

| Issue | Implemented and tested | Still required before issue closure |
| --- | --- | --- |
| #10 mechanisms | Versioned coverage records, reviewed routing, severity-first queue, distinct dispositions and budget deferral; bounded admission of proposer `mechanisms`/`falsifiers` JSON as unverified records with per-entry rejection reasons; pure planned route dispositions | Attach admission and dispositions to the `ahl improve` receipt and queue; integrate real proposal sessions and versioned evidence; evaluate composed repairs |
| #11 skills | Injection/observable activation separation, routing-condition reports, negative-transfer rejection, approved procedure hypothesis API | Native activation instrumentation, independently graded routing/transfer pack, live utility and compiler evaluation |
| #12 search | Candidate identity/scope/hash checks, deduplication, hard safety feasibility, Pareto frontier, bounded development callbacks, final reserve, durable state, single final callback, conflict-checked composition | Version-verified reflective CLI proposer, actual inference resource accounting, full resume/recovery and algorithm comparisons; optional halving is not implemented |
| #13 adapters | Explicit typed authorization, bounded deterministic error presentation, immutable original status/payload, unsupported-runtime reporting | An actually supported tool proxy/interception runtime and independently graded adapter effectiveness; no runtime flags are invented |
| #14 retrieval | Approved reviewed local lexical index, bounded context, stable corpus revision, explicit abstention without calibration | Reviewed incident corpus, calibration/OOD evaluation, optional TF-IDF/embeddings comparisons and downstream utility tests |
| #15 promotion | Private write-ahead snapshots, exact hashes/modes, owned-path transactions, manual preview/rollback, partial-write recovery, version-attributed fixed-window observation | Crash/power-loss hardening across every boundary, monitor-to-watch integration, authenticated outcome lineage and any explicitly configured automatic rollback policy |
| #16 benchmarks | Within-pair runtime validation, separate model strata, missing-setting reporting, measured-cost API with supplied dated prices, private/mixed export rejection, escaped HTML/Markdown summaries, credential-free package CI | Trusted live workflow with broker-scoped credentials and resource enforcement, public task evidence and complete reports of per-task intervals/exposure |
| #17 bandit research | Seeded Beta-Bernoulli baseline, approved read-only arms, delayed-reward attribution, idempotent updates, missing rewards, bounded simulation, five strategy comparisons, drift/sparse/delayed/harmful scenarios, identifiability refusal | Preregistered independently graded coding-task study, final uncertainty analysis, explicit pilot budget and training/privacy/license/access review |

## Boundaries and interpretation

`MechanismRecord` requires evidence, a boundary, a competing explanation and a
falsifier before a route can be actionable. Expected negative tests route to
no-change; unknown capture remains insufficient evidence. One rejected mechanism
does not erase another. Development validation yields a recommendation, never
certification. Queue/search callbacks are controller extension points: their work
unit charges do not establish measured token/time/cost accounting for an arbitrary
external callback. A production integration must account for every nested action.

`mechanisms.from_proposal` admits untrusted proposer output of the form
`{"status":"candidate","mechanisms":[{...}],"falsifiers":[{"mechanism_id":...,"falsifier":...}]}`.
It is pure and total over the proposal: any value yields a
`proposer-mechanism-admission-v1` receipt listing admitted entries and every
rejected location with a reason. Citations must come from the controller's
`evidence_refs` allowlist; components and severities must use the router
vocabulary; versions come from the controller. Fields that claim authority
(`disposition`, `status`, `validated`, `reviewed_component`, versions, history)
reject the entry. Identical repeats are duplicates; one identity with differing
or malformed variants is contradictory and none of its variants is admitted.
A record carries one falsifier, inline or attached by ID; several distinct ones
reject it rather than selecting one. Count, list, text and identifier bounds
are explicit and reported (`None` means unlimited); an oversized entry is
rejected and an oversized list rejects the whole proposal, never a truncated
prefix. Admitted entries are `proposed_unverified`: records keep disposition
`proposed` and the unreviewed `insufficient_evidence` component, with the
proposer's claim kept only as `proposed_component`. Admission is bookkeeping;
it establishes no diagnosis, cause or validated repair.

`router.dispositions` plans the queue without evaluating, charging or mutating
records or the budget. Routes come only from a separate `reviewed` mapping and
explicit expected-negative labels, never from a record's own component, so
unreviewed proposer records defer as `route_requires_review` and unauthorized
adapter routes defer. Order, deduplication and budget charges match
`run_queue`; eligible records stay `proposed`. Both receipts are private
profiles rejected by public export. Neither is attached to the `ahl improve`
receipt yet; that integration remains open in #10.

Search persists charges and stages before callbacks. Restart returns recorded
state without repeating charged work. An interrupted stage requires explicit
recovery; it does not rerun inference automatically. Certification is a separate
final callback; its feedback cannot generate another candidate in the same run.
A composed patch gets a new identity and needs interaction evaluation. Missing
metrics do not become free execution or superior performance. Pareto point
estimates are development selection, not a causal or confidence claim.

Skill injection is availability evidence, not activation. Missing runtime signals
remain unknown; natural routing, preload, negative routing and transfer are separate
conditions. The procedure compiler returns a bounded project hypothesis and never
installs globally. The typed adapter preserves an exact original tool envelope and
adds presentation metadata only. It cannot call a tool, retry an action, expand
network/filesystem authority or turn an error into success. Runtime integration
currently reports unsupported.

Lexical retrieval accepts only approved reviewed summaries, retains sampling
provenance and returns untrusted evidence, including any injection text. It never
executes retrieved instructions. Signal-enriched samples are not prevalence
samples. No calibrated diagnosis is produced by the new API: without an implemented,
independently assessed calibration protocol it always abstains. Existing optional
classifier experiments remain diagnostic experiments, not promotion oracles.

## Transactions and monitoring

The direct `adopt` API now records exact before/after bytes and modes before writing.
Journal state stays private under the user state directory. Only owned harness
paths are eligible. Manual rollback checks active lineage and every affected
current file before restoring exactly that change set; additions/deletions are
reversed without resetting the repository. Any intervening bytes, mode, symlink,
hardlink or later deployment conflicts. A partial transaction can resume only
when every current file matches a recorded before/after state. No global settings
or unrelated files are restored.

`ahl promotion PROJECT --deployment ID` previews without changing project files.
`--action rollback` performs the guarded manual rollback; `--action recover`
resumes a recognized partial transaction. These commands take the project lock.
No automatic rollback or background monitor starts on installation.

Observe-only monitoring uses a frozen exposure window and task-start deployment
mapping. Late A outcomes cannot trigger rollback of B. Duplicate, missing and
ambiguous observations are not fabricated successes. A verified critical event
pauses for manual review; statistical drift attribution and automatic reversion
are not implemented. A `stable` window means its explicit exposure conditions
were reached without an observed critical event, not that rare-harm risk is zero.

## CLI and reports

### Controller trajectory reports

An explicit Responses evaluation can opt a task into the frozen
[trajectory profile](evaluation-foundation.md#frozen-controller-trajectory-assertions)
through its existing oracle bundle. It uses controller-owned observations and the
existing artifact grader, then retains separate `grader` and `controller_grade`
records. A frozen repeated plan receives a `trajectory_analysis` over scheduled
trial outcomes using the existing statistics engine. Unknown required evidence
blocks completion; known critical violations remain visible despite grader or
execution errors. These are conditional local results, without authenticated
native identity, historical controller authority or automatic adoption.

`ahl benchmark-report --input RESULTS.json --output NEW_DIRECTORY` also recognizes
these saved evaluation results. It writes bounded private JSON, Markdown and HTML
with artifact outcomes, trajectory statuses, combined outcomes and critical/unknown
flags. Imported claims are explicitly `imported_unverified`; the command does not
recreate live receipts or rerun statistical decisions from those claims. It omits
raw argv and controller traces. `--public` refuses this mode even if imported
metadata claims synthetic provenance or publication approval. Existing matched
benchmark records and controller-history reports keep their own contracts.

Artifact-only task bundles need no migration. Opt-in policies require the
Responses executor; other executors reject them before worker execution. Changed
policies or application/runtime dependencies require new oracle and plan pins,
with fresh executions in both arms. #10 and #123 can consume these receipts for
their distinct constraint/behavior semantics; this profile does not infer those
semantics from arbitrary commands or import unverified traces as controller data.

- `ahl research-simulate --tasks 200 --scenario delayed` runs synthetic controlled
  comparisons only. The rule comparator is explicitly fixed lexical in this initial
  simulator; it is not a validated learned router. The simulator does not claim
  stationary guarantees under drift, reconstruct counterfactuals from arbitrary
  logs, or automatically start RL. Its RL decision is **insufficient evidence**;
  the output lists the missing trajectories, held-out benefit and access/budget.
- `ahl incident-search --input APPROVED.json --query TEXT` searches a bounded
  local corpus. Private summaries/results must remain private.
- `ahl benchmark-report --input RESULTS.json --output NEW_DIRECTORY` writes a
  private local report. `--public` requires explicitly approved synthetic/public
  provenance for every input and rejects all private-derived aggregates. It creates
  an eligible local export; it does not publish online. Redaction cannot authorize
  private-result publication.

Dollar cost remains unknown without measured usage and supplied dated pricing.
Reports retain failed, skipped, inconclusive and not-run statuses, keep runtime
strata separate and never turn fake-agent/replay results into real-model evidence.
Packaging CI has no provider secrets, paid workflow, schedule or public result
uploads. Existing Python 3.11–3.13 tests exercise the new modules; packaging smoke
checks a clean wheel and versioned archive without inference dependencies.
