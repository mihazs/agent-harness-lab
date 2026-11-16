# Documentation

Choose a starting point by what you need. Each page is listed under its primary
reader purpose.

## Learn

- [First diagnosis](getting-started.md): reproduce a safe, synthetic diagnosis and interpret its output.
- [Offline audit walkthrough](audit-walkthrough.md): inspect a synthetic harness without a model.

## Complete a task

- [Install and update](installation.md), including [keeping your global instructions in step](installation.md#keep-your-global-instructions-in-step), and [check readiness or initialize a project](onboarding.md).
- [Analyze, improve, or monitor a project](workflows.md); [improve until an acceptance score is reached](improve-until-score.md); [improve from past sessions](improve-from-past-sessions.md); [verify a new Claude Code release](verify-claude-release.md); [analyze an explicit memory session](analyze-memory-session.md); [reuse analysis work on unchanged input](incremental-analysis.md); [inspect CLI help](cli-help.md).
- [Audit a long-lived checkout whose inventory is blocked](audit-long-lived-checkout.md), [repair a supported skill header](repair-skill-header.md), [choose the project or user-level scope](improve-scope.md), or [inspect a private source journal](inspect-source-context.md).
- [Annotate pull requests with the static audit](audit-pull-requests.md), [find where agents ignored instructions](detect-ignored-instructions.md), or [repair a harness whose instructions are ignored](repair-ignored-instructions.md).
- [Stage a presentation adapter under a controller-owned manifest and roll it back](stage-an-adapter.md).
- [Review source relationships](source-lineage-review.md), [declare a regression](regression-proposals.md), [review a case](regression-review.md), or [admit it to a development bank](regression-admission.md).
- [Check task lineage with blind adjudication](blind-lineage-adjudication.md), [review a conditional final bank](conditional-bank-review.md), [run fixed repeated-bank improvement](repeated-improvement.md), [declare capability areas and tag bank tasks](declare-capability-areas.md), or [score a harness per capability area](score-by-capability-area.md).
- [Convert a task-plus-text-expectations case list into a native bank](convert-a-case-list.md) and run a before/after comparison on it.
- [Preflight experiment precision](experiment-precision.md), [add a Bayesian decision score to a plan and read it](bayesian-decision-how-to.md), [search with preservation evidence](guarded-search.md), or [review grader corrections](grader-corrections.md).
- [Record comparisons and quarantine graders](oracle-history.md) or [generate private history reports](history-reports.md).
- [Share a forge API quota safely](forge-rate-limits.md), [manage repository work](project-management.md), [verify release publication](release-publication.md), [measure startup, throughput and memory](performance-benchmark.md), or [contribute](../CONTRIBUTING.md), [try AHL on a real project without changing it](isolated-project-check.md), or [validate AHL on a real project](real-project-acceptance.md) with a first-use run, a before/after evaluation and incremental runs.

## Look up behavior and contracts

- [Global instruction region markers and typed reasons](global-instructions-reference.md), [command overview](command-reference.md), [static audit contract](static-audit.md), [analysis methods](analysis.md), [evaluation contracts and prerequisites](evaluation-foundation.md), and the [evaluation manifest contract](evaluation-manifest-contract.md), including its [capability area declarations](evaluation-manifest-contract.md#capability-area-declarations).
- [Campaign specification and planned records](campaign-specification.md).
- [`ahl score` reference](score-reference.md): the per-area report, its inputs, output fields, evidence states and reasons.
- [Loop declaration, state machine and controller](loop-reference.md): the acceptance score declaration, its sources and precedence, the evaluate > improve > evaluate states, stop criteria, decision table and order invariants, the controller's ports, rounds, budget reserve and round ledger, the default of `ahl improve` and `--single-pass`, the receipt and exit codes, and the simulation test-bed.
- [Source contracts](source-contracts.md), [source lineage review](source-lineage-reference.md), [regression review records](regression-review-reference.md), [telemetry](telemetry.md), and [explicit memory page captures](memory-page-capture.md).
- [Bank qualification](bank-qualification.md), [synthetic adversarial bank](adversarial-bank.md), [content grouping](content-grouping.md), and [evaluation interchange](evaluation-interchange.md).
- [Bayesian decision score reference](bayesian-decision-reference.md): the `bayes_decision` spec field, the output block with the recorded input behind each number, reasons, verdict meanings and the six synthetic fixtures.
- [Repeat reliability profile](repeat-reliability.md), [controlled experiments](controlled-experiments.md), [controller-loaded instructions](controlled-agents.md), [controller source observations](controller-sources.md), and [implementation prerequisite observations](implementation-prerequisites.md).
- [Decision receipts](decision-receipts.md), [diagnostic corpus](diagnostic-corpus.md), [triage policies](diagnostic-triage.md), [cascade evaluation](diagnostic-cascade.md), [judge controls](diagnostic-judge.md), [backend prediction envelope](diagnostic-backend-envelope.md), [retrieval leakage check](diagnostic-retrieval-leakage.md), and [audit sampling](audit-sampling.md).
- [Instruction adherence](instruction-adherence.md): the sessions file, checkable sentence shapes, three-valued judging, the report and its calibration.
- [Instruction repair](instruction-repair.md): candidate mechanisms, repair classes, the repair plan and the recorded-pair screen.
- [Drift monitoring](drift-monitoring.md), [permission audit](permission-audit.md), [runtime boundary](runtime-boundary.md), [resource decisions](resource-decisions.md), and the [harness degradation report](harness-degradation-report.md) with its claim ladder and metric contracts.
- [Report integrity fields](report-integrity.md): seeded-run provenance, evidence-strength labels, frozen-grader enforcement, receipt-sourced numbers and the pre-edit check.
- [Vacuity labels](vacuity-labels.md): the closed set of five labels for an evaluation that proves nothing, where each surface applies them, and the audit of reported numbers.
- [Typed exit receipts](typed-exit-receipts.md): the closed vocabulary of statuses, reasons and stages every non-zero `ahl improve` exit carries.
- [Before/after guard](before-after-guard.md): the paired verdict, the frozen-inputs identity of both arms and the investigation record of a regressed candidate.
- [Incremental runs](incremental-runs.md): what a second `ahl improve` reads from the first: applied, ignored, regressed or edited candidates, historical beside fresh scores, and the finished regression it does not repeat.
- [Stage budgets and early stops](budget-stages.md): the construction time box, feasibility check, uninformative stop, refinement reuse and per-stage receipt.
- [First-use stress matrix](stress-matrix.md): the injected runtime, history, harness-file, concurrency, interruption and budget failures and the typed end state each must reach.
- [One-call improvement](one-call-improvement.md), [owned development variants](owned-development-variants.md), [protected diagnostic access](protected-diagnostic-access.md), and [canonical construction exposure](canonical-construction-access.md).
- [Optimization strategy registry](optimization-strategies.md), [preservation cohorts](preservation-cohorts.md), [sequential experiments](sequential-experiments.md), and [side-by-side review model](side-by-side-reports.md).
- [Private memory store](memory-store.md): create, verify, rebuild and clean the opt-in evidence store.
- [Stage reuse planner](stage-reuse-planner.md): node keys, the stage graph, reuse rules and the `ahl-stage-reuse-v1` receipt.
- [Live evaluation gate](live-evaluation-gate.md): the manual dispatch inputs and bounds, the refusal reasons and the static workflow trust policy; the gate runs no model.
- [Comparability engine](comparability-engine.md): the gates, verdicts and strata that decide whether two evaluations may be compared.
- [Evidence reuse contract matrix](memory-reuse-contract-matrix.md): which stores are reusable, replayable or historical-only, with synthetic reuse fixtures.
- [Harness snapshots and baselines](harness-snapshots.md): name the harness of a checkout, diff two snapshots, and declare a baseline before outcomes exist.

## Understand the context

- [Why a managed region of the global instruction files](global-instructions-rationale.md), [newcomer experience decisions](onboarding-rationale.md) and [analysis method-selection rationale](analysis-method-rationale.md).
- [Why the loop evaluates, improves, then evaluates](loop-rationale.md): why the order is fixed, why scores carry intervals, why each round is a look, and what `accepted` does not mean.
- [Why there is one skill](single-skill-rationale.md), [why evidence reuse is split by authority](memory-reuse-rationale.md) and [why hypothesis probes are separate from analysis](hypothesis-probes.md).
- [A paired Bayesian decision score with a ROPE](bayesian-decision-score.md): why a numeric, non-LLM better, equivalent, worse or insufficient-evidence score runs beside the unchanged Hoeffding path, how it treats unknown outcomes, how its false-recommendation rate was calibrated with the seeded power simulator under a frozen protocol, its descriptive-only desirability and Pareto epsilon-indicator report, its evidence status (no peer-reviewed work applies the method to agent harness scoring) and its limits.
- [Why instruction-adherence findings are separate and abstain](instruction-adherence-rationale.md), and [why a repair plan is a recommendation](instruction-adherence-rationale.md#why-a-repair-plan-is-a-recommendation).
- [Where a tool-result adapter may attach to a runtime](adapter-runtime-decision.md): the evidence, decision and open blockers for the identity bridge.
- [Scientific sources and limits](../skills/harness-self-improvement/references/evidence-to-design.md), [diagnosis criteria](../skills/harness-self-improvement/references/trace-diagnosis.md), and [execution contract](../skills/harness-self-improvement/references/execution.md).

## Historical and maintainer material

- [Initial validation](validation.md) is a dated record, not a current suite count or evidence of real-world effectiveness.
- [Community growth plan](community-growth.md) and [publication drafts](launch-drafts.md) are maintainer proposals.
