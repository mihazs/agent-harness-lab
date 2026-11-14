# Controlled experiment APIs and operational recovery

These are tested implementation stages for #10–#17, not completed live studies.
They are standard-library, local-only APIs. Search, procedure compilation and
policy experiments are off unless explicitly invoked. Imports do not start an
agent, download a model, alter runtime permissions, install a skill or train a
model. Existing cooperative evaluation remains recommendation-only.

| Issue | Implemented and tested | Still required before issue closure |
| --- | --- | --- |
| #10 mechanisms | Versioned coverage records, reviewed routing, severity-first queue, distinct dispositions and budget deferral; bounded admission of proposer `mechanisms`/`falsifiers` JSON as unverified records with per-entry rejection reasons; pure planned route dispositions; admission and the private coverage report attached to the `ahl improve` receipt as `mechanism_coverage`; private `mechanism-coverage-v1` JSON/Markdown coverage report separating observed patterns, supported diagnoses, development recommendations and validated improvements; immutable `PredictionRecord` with an append-only hash-chained freeze ledger and pure `prediction-reconciliation-v1` reconciliation (`diagnostics/predictions.py`); versioned deterministic constraint definitions with three-valued `constraint-report-v1` observations over captured events (`diagnostics/constraints.py`) | Reviewed route dispositions and queue integration in `ahl improve`; versioned evidence beyond the analysis brief; step localization and competing hypotheses over constraint results; evaluate composed repairs; wire predictions into proposal sessions and the `ahl improve` receipt |
| #11 skills | Injection/observable activation separation, routing-condition reports, negative-transfer rejection, paired skill-present/absent transfer report under a predeclared target/control policy (`paired_transfer`), approved procedure hypothesis API; observable-requirement procedure compliance with declared alternatives and separate safety checks (`procedure_compliance`); pinned Claude 2.1.316 declared `Skill` names and exact project `SKILL.md` read requests as content-free `declared_invocation`/`file_read` signals with confidence labels (`observed_declared`, never activation); evaluator trials record `skill_availability` bundle provenance for both the `skills` and `harness_paths` routes | Observed activation evidence, Codex skill signals, shadowing/effective-path detection, wiring activation signals into reports, independently graded routing/transfer pack, wiring compliance into transfer reports, live utility and compiler evaluation |
| #12 search | Candidate identity/scope/hash checks, deduplication, hard safety feasibility, Pareto frontier, bounded development callbacks, final reserve, durable state, single final callback, conflict-checked composition | Version-verified reflective CLI proposer, actual inference resource accounting, full resume/recovery and algorithm comparisons; optional halving is not implemented |
| #13 adapters | Explicit typed authorization, bounded deterministic error presentation, immutable original status/payload, unsupported-runtime reporting; declarative `AdapterSpec` capability contract with a non-executing candidate review (`review_candidate_spec`) that rejects expanded authority; a runtime decision record, an offline runtime probe and an identity-only bridge with a capability receipt, exercised through a fake runtime only | A live run proving the bridge hook executes in the supported runtime, an isolated non-identity transform, and independently graded adapter effectiveness; no runtime flags are invented |
| #14 retrieval | Approved reviewed local lexical index, bounded context, stable corpus revision, contrastive supporting/contradicting/control groups, explicit abstention without calibration | Reviewed incident corpus, calibration/OOD evaluation, optional TF-IDF/embeddings comparisons and downstream utility tests |
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
profiles rejected by public export. The route-disposition plan is not attached
to the `ahl improve` receipt yet; that integration remains open in #10.

`ahl improve` admits every `candidate` proposal it reads through
`mechanisms.from_proposal` and adds a compact `mechanism_coverage` field to its
receipt (`workflow.json`). The proposer is told the entry format; the allowlist
of citable evidence is the analysis brief it was shown, meaning its listed
mechanism IDs (such as `Bash/tool_reported_error`) and its observed weak-outcome
signals, never an unknown or unobserved signal. The admission receipts
(`mechanism-admission-<attempt>.json`) and the coverage report
(`mechanism-coverage.json` and `.md`) stay in the private run folder. Every
admitted record is `proposed` and unreviewed, so a tie, an accepted patch or an
evaluation error leaves it listed as `open`, never `resolved`; the controller
declares no capture completeness, so `coverage_complete` is `false` and unseen
mechanisms stay possible. An entry the contract refuses, including the older
free-text form, is counted with its reason, and a run whose proposal admits
nothing reports `no_mechanisms_recorded`. The receipt field is advisory: a
failure to write it is reported as `unavailable` and never changes the run
status. It records bookkeeping, not a diagnosis, a validated repair or a
statement of which mechanism a combined patch repaired.

`coverage.report` summarizes records, or the `run_queue` output, as a private
`mechanism-coverage-v1` report without evaluating, charging or mutating
anything; `coverage.render_markdown` renders it with every recorded string
escaped. Each entry keeps its responsible component, claim level, disposition
and coverage state separate. The claim level is `observed_pattern` unless the
record is actionable under a reviewed component (`supported_diagnosis`); a
`recommended` disposition is a `development_recommendation` awaiting
certification, and only `validated` is a `validated_improvement`. A disposition
the record cannot support stays `open` with the
`disposition_exceeds_supported_claim` limitation. A record without captured
evidence is `unknown_capture`, with claim level `none`, whatever disposition it
carries. Complete coverage requires at least one mechanism, every entry
`resolved` (validated, rejected, already covered or outside scope) and an
explicit `capture="complete"` declaration; the default `unknown` and `partial`
capture report that unseen mechanisms are possible. A repeated key is
suppressed only when its derived entry and history match the first record's;
any other repeat, such as one without captured evidence or without a falsifier,
is listed as a conflict and keeps the key open (`conflicting_records_for_key`)
in either order. Unknown exposure denominators, suppressed duplicate keys and
mechanism IDs recorded under several evidence/component versions are listed. Routes and claim levels are hypotheses,
not causal proof.

`PredictionRecord` freezes a candidate's expected observable effect, regression
risks, competing explanation, falsifier and comparison criterion before its
outcomes are read. `PredictionLedger.register` keeps hash-chained receipts; the
same content is idempotent, changed content under the same ID is rejected and a
changed candidate, bank or grader identity needs a new record. A prediction
registered after its candidate's outcomes were read is recorded with
`preregistered: false` and reconciles as `not_comparable`. Records may name only
development task IDs; protected tests are described by family/property text and
protected IDs are rejected. `reconcile` returns one row per registered
prediction with status `supported_under_test`, `contradicted_under_test`,
`inconclusive`, `not_tested` or `not_comparable`. Missing, duplicate, unknown,
stale or invalid-evaluator evidence never becomes support (an invalid grader or
runtime is routed to evaluator/runtime work, not blamed on the agent); a
baseline-already-passes pair is `non_discriminative`; a control regression
contradicts the prediction. A stochastic prediction is `inconclusive` unless the
paired-analysis verdict from the evaluation owner is supplied; no statistics are
computed here. A multi-component prediction without an ablation design reports
`attribution: unresolved`. Predictions are self-selected hypotheses: the report
states its eligible denominator, never certifies and is not diagnostic accuracy.

`coverage.report(..., predictions=reconcile(...))` adds that table to the
private coverage JSON and Markdown: expected change, status, reason, attribution
and limitations per prediction, with contradicted, untested and non-comparable
rows always listed. A `validated` mechanism with a contradicted prediction is
reopened (`prediction_contradicted`) and cannot complete coverage; a prediction
naming an unrecorded mechanism is flagged `mechanism_not_recorded`. Without a
table the report says `no_predictions_reconciled`. The table is bookkeeping, not
diagnostic accuracy or certification.

`diagnostics/constraints.py` evaluates reviewed `constraint-definition-v1` records
over the normalized events of one case: `tool_contract`, `temporal_prerequisite`,
`authorization_scope`, `state_consistency` and `outcome_integrity`. A definition
needs a trusted origin (`reviewed`, `project_contract` or `controller`; a
model-written constraint has none) and a scope (`any` or the evidence scope the
caller passes). `evaluate` and `report` return `constraint-evaluation-v1` and
`constraint-report-v1` rows with status `satisfied`, `violated` or `unknown`, an
enumerated reason, the required and missing inputs, and bounded event references.
A violation the events show holds under any capture; `satisfied` needs a capture
the controller declared `complete` (a row reporting `capture_complete: false`
lowers it) and at least one exercised event, except that no call to a forbidden
tool is itself the claim. Missing order, fields, names or a final outcome give
`unknown`; order is read only from a capture ordinal (or a declared subordinal
inside one segment), never from timestamps, and a tie or a cross-file pair stays
unordered. `outcome_integrity` is violated by a success or failure label that lacks a valid
grader or a reviewer's reference. A result is an observation: it names no cause, component or route,
carries no model-written text, never validates a mechanism and is not publishable.
The earliest visible violation is not the root cause; localization, competing
hypotheses and routing remain separate steps, and a reviewed constraint is
evidence a `MechanismRecord` may cite, not a replacement for its boundary,
competing explanation and falsifier.

`run_queue` treats a record's component as its reviewed route and checks it
through `route()` with the same held dispositions as `dispositions`. A
`tool_adapter` record is deferred as `adapter_route_not_authorized` without
invoking the callback or charging the budget unless the caller passes
`authorized_adapters=True`.

Search persists charges and stages before callbacks. Restart returns recorded
state without repeating charged work. An interrupted stage requires explicit
recovery; it does not rerun inference automatically. Certification is a separate
final callback; its feedback cannot generate another candidate in the same run.
A composed patch gets a new identity and needs interaction evaluation. Missing
metrics do not become free execution or superior performance. Pareto point
estimates are development selection, not a causal or confidence claim.

`evaluation.skills.activation_receipt` builds a private `ahl-skill-activation-receipt-v1`
for a single normalized case. `activated` means a declared Skill invocation or
file-read request was captured, with its signal confidence retained; it does not
prove that the skill loaded, ran or caused an outcome. A target is `not_observed`
only with a declared complete capture containing at least one skill signal; otherwise
missing signals are `unknown`. Codex and unrecognized native sources cannot claim
Claude skill signals. Current pinned Codex and Claude native captures are partial, so they cannot establish non-observation. Evaluator trial results carry an
unknown receipt because their stdout has no verified link to pinned native captures.
`evaluation.skills.attach_activation_receipt(trial, events, case_id=...)` returns
a trial with a receipt for explicitly selected normalized events; the trial-to-case
attribution is caller-declared and unverified, and mixed case IDs are rejected.
Raw CLI stdout does not verify that linkage. The supported CLI takes an evaluator
`results.json` and a normalized events JSONL (for example, `events.v2.jsonl` from
`ahl analyze`) and prints a private receipt without modifying either input:

```sh
ahl skills activation-receipt --results RESULTS --events EVENTS --task TASK \
  --version candidate --repetition 0 --case-id CASE_ID
```

The caller selects the case and remains responsible for the unverified association.
Both inputs must be regular files with no symlink in their path, each at most 64 MiB;
the trial selector must match exactly one result. Receipts are private and must not be published.
The estimand is the effect of skill availability (intent-to-treat), with all eligible
trials retained irrespective of their receipt; no receipt alone establishes a causal effect.

Each evaluator trial also records `skill_availability`, one row per staged skill bundle
delivered through either the `skills` route or `harness_paths` (`.claude/skills/<name>/` and
`.agents/skills/<name>/` files): `route`, `name`, `state` (`present` or `absent`),
`entry_sha256`, `bundle_sha256` and `activated`. Identical staged content has equal hashes
through both routes, so availability is comparable while only `route` differs. A version
that lacks the entry point is `absent` (a removal), and `activated` is always `null`:
placement is not discovery, reading or use. For `harness_paths`, the bundle hash covers
only the files the manifest lists.

Skill injection is availability evidence, not activation. Each evaluator trial records
`skill_preload_requested` as true when the manifest requests a preload prompt for
at least one explicitly listed skill. False also covers trials with no listed skills;
this field does not show that a prompt was sent or a skill was discovered or used.
Missing runtime signals remain unknown; natural routing, preload, negative routing
and transfer are separate conditions. `evaluation.skills.paired_transfer(records, policy=...)` compares the
`present` and `absent` arms over task roots matched by task and root identity.
The policy must declare its target conditions, its unrelated control conditions
and `max_control_loss` (0 to 1); its SHA-256 is reported. Preload can only be a
diagnostic condition. Repeats are averaged within a root, so the root is the unit.
Only boolean outcomes from an executable grader count. Unverified, unidentified
and single-arm records are counted as excluded or unmatched, never as failures,
and their regression or activation flags are ignored. A critical regression or a
mean control loss beyond the tolerance rejects the skill, even when its target
tasks improve. An observed activation in the absent arm, such as a shadowed copy,
makes the comparison inconclusive, as do missing matched target or control roots
and any unmatched or excluded record in a declared condition, because attrition
in one arm can hide negative transfer. Otherwise the report stays `uncertified` until a
frozen paired experiment runs through the evaluation gate. The main contrast never
filters on activation. Activation counts as observed only for `activated: true`
with an evidence reference; `activated: false`, an injected hash or a bare label
stays unknown. Observed activations on controls are reported as false activations.
Success among observed activations is reported as descriptive, not causal.
`evaluation.skills.procedure_compliance(spec, events=..., artifacts=...)` grades one
case against externally checkable requirements instead of phrasing. The spec declares
exactly `alternatives` (at most 16 named lists of at most 32 requirements, so another
correct procedure passes) and `safety` (requirements that stay independent of the
procedure). A requirement is `tool_call` (observed), `no_tool_call` (not observed) or
`artifact` (a named check from an executable grader passed). Each resolves to `met`,
`unmet` or `unknown`. An absent tool call is `unmet` only when every row of a nonempty
capture declares `capture_complete`; a partial or empty capture leaves it `unknown`,
and so does an artifact check that is missing, not boolean or not from an executable
grader. A skill-activation signal or a keyword never satisfies a requirement. The case
is `compliant` when one alternative is fully met, `noncompliant` when every alternative
has an unmet requirement, and `unknown` otherwise; `safety_state` is `satisfied`,
`violated`, `unknown` or `none_declared`. The private report carries the spec SHA-256
and `publication_allowed: false`, and it is neither task success nor a causal effect.
The procedure compiler returns a bounded project hypothesis and never
installs globally. The typed adapter preserves an exact original tool envelope and
adds presentation metadata only. It cannot call a tool, retry an action, expand
network/filesystem authority or turn an error into success. Runtime integration
currently reports unsupported, with the documented candidate and its open blockers as data.

`AdapterSpec` (`ahl.adapter-spec.v1`) is the opt-in capability contract for one
presentation adapter: ID, version, implementation SHA-256, target tools, the
`tool-envelope-v1` input and `presented-envelope-v1` output contracts, the
`identity` and `structured-error-v1` transforms, side effects, filesystem,
network, credential and nested-tool grants, resource limits, the rollback digest
and the declared runtime. It is data and never runs. `AdapterSpec.from_mapping`
rejects unknown and missing fields instead of ignoring them. A spec with any side
effect or any filesystem, network, credential or nested-tool grant is rejected,
and so are limits above 64 KiB of input, 128 KiB of output or 10 seconds.
`review_candidate_spec(authorized, candidate, authorization)` compares an
untrusted candidate with the controller-owned spec without model judgment. It
rejects a candidate that adds a tool, transform or grant, raises a limit, renames
the adapter or changes its contracts or runtime. New implementation bytes are
admissible only when `previous_digest` equals the authorized spec's
`content_digest` (the implementation SHA-256, reported as
`authorized_content_digest`), not the spec digest `authorized_digest`. With
unchanged bytes, `previous_digest` must stay as authorized. The verdict
`within_authorized_capabilities` means only that the candidate stays inside the
declared authority. The review never executes adapter code, never promotes, and
reports the runtime as unsupported. `verify_adapter_bytes` rechecks bytes against
the declared digest. Nothing calls these APIs by default.

`evaluation.adapter_bridge` is the identity-only bridge for the one documented
runtime integration, the Claude Code `PostToolUse` and `PostToolUseFailure` hooks;
[the decision record](adapter-runtime-decision.md) holds the evidence, coverage and
blockers. `probe_runtime(runtime, version)` is offline: only the pinned Claude Code
version is `declared`, Codex and any other version are `unsupported`, and nothing is
ever `verified` by it. `Bridge` rejects, at construction, an unsupported runtime or
version, a spec outside the project authorization, a spec without the `identity`
transform, and adapter bytes that differ from the declared digest. It never loads or
runs those bytes. `Bridge.handle(stdin_bytes)` returns hook stdout bytes. Each event
becomes a `tool-envelope-v1` bound to the run (`session_id`) and call
(`tool_use_id`); a failure event keeps `error` status and no other field can change
it. The identity result is verified against the original and the reply is an empty
object, so the runtime keeps the original output. A duplicate with the same content
is a recorded no-op, a duplicate with different content fails without replacing the
first, a result for a cancelled call is dropped, and results may arrive in any
order. The metadata-only `ledger()` is separate from the private `provenance()`
envelopes. A malformed, oversized, unauthorized, other-run or conflicting event
fails: a `required` bridge answers `continue: false` and stops the affected
execution, while `optional-identity` records the failure and leaves the original
output. The policy is frozen at construction and no failure becomes a success.
`capability_receipt()` binds the bridge source, adapter, spec and authorization
digests to the calls and to any model-visible results the caller observed; without
observations the application state stays `unknown`, and the only accepted source is
the test `fake-runtime`, which sets `runtime_verified` to false. `verify_receipt`
fails after any change to the authorization, spec, adapter bytes, bridge source or
receipt content, or after an observed mismatch. A hook command, deployment of its
configuration and the isolated transform worker are not part of this bridge.

Lexical retrieval accepts only approved reviewed summaries, retains sampling
provenance and returns untrusted evidence, including any injection text. It never
executes retrieved instructions. Signal-enriched samples are not prevalence
samples. No calibrated diagnosis is produced by the new API: without an implemented,
independently assessed calibration protocol it always abstains. Existing optional
classifier experiments remain diagnostic experiments, not promotion oracles.

### Contrastive incident retrieval

`LexicalIndex.contrast(text, hypothesis, control_labels=(), top_k=None)` groups
the same lexical matches around one label hypothesis instead of returning a single
ranked list. `supporting` matches carry the hypothesis label, `contradicting`
matches carry other labels, `controls` carry a caller-declared successful-control
label, and `unresolved` matches are unlabeled or labeled `unknown`/`ambiguous`.
Those two labels are reserved and cannot be a hypothesis or control. `top_k`
bounds each group separately, so close supporting matches cannot crowd out
counterexamples. Each group reports its match count and distinct roots; the report
also counts roots shared by supporting and contradicting matches, hypothesis-labeled
incidents that did not match, and unmatched corpus records.

Each result carries the incident ID, a SHA-256 of its root ID (never the raw ID),
labels, evidence references, score, provenance, sampling and source kind. Summaries
from `private` records are withheld (`summary: null`,
`summary_withheld: "private_source"`). `evidence_state` is `no_lexical_support`,
`mixed` or `supporting_only`; it describes lexical neighbours only. The report
always states `diagnosis: "hypothesis_only"`, `causal_claim: false`,
`prevalence: "not_estimable_from_lexical_neighbours"` and
`promotion_authority: false`: similarity does not establish a shared cause, and
a neighbour count is not a prevalence estimate or a calibrated probability.

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
restores the before snapshots for a recognized unfinished transaction. These commands
take the project lock. A process killed after writing a temporary replacement can
leave an `*.ahl-*` file beside the target; recovery does not delete it. A crash after
the committed journal record but before the active pointer update is refused by
`recover` because the record is already committed; it requires inspection. Synthetic process-death checks cover
these boundaries; they do not establish durability across power loss.
No automatic rollback or background monitor starts on installation.

Observe-only monitoring uses a frozen exposure window and task-start deployment
mapping. Late A outcomes cannot trigger rollback of B. Duplicate, missing and
ambiguous observations are not fabricated successes. A verified critical event
pauses for manual review; statistical drift attribution and automatic reversion
are not implemented. [Frozen drift plans](drift-monitoring.md) separate capture
alerts from task-mix and verified-performance exposure without a statistical test. A `stable` window means its explicit exposure conditions
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
flags. The private JSON summary retains bounded child assertion identities, statuses
and coverage claims for grouped assertions, checking child IDs against their parent
and position and requiring canonical digests. Root identifiers remain imported text;
the report is private and is not a general secret scrubber. Imported claims are explicitly `imported_unverified`; the command does not
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
  `--hypothesis LABEL` (with optional repeatable `--control-label LABEL`) returns
  an `incident-contrast-v1` report instead; see
  [contrastive retrieval](#contrastive-incident-retrieval).
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
