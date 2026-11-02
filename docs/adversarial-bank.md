# Synthetic adversarial final-artifact probes

The exposed `adversarial-v1` development bank contains 14 tasks: benign/adversarial
pairs across repository text, retrieved data, instruction and skill spoofing,
counterfeit reference/grader text, counterfeit receipts, and synthetic canary
export. Related variants share three declared roots/families. They are not 14
independent samples, secret tests, native skill activation or model evaluations.

Each task requires a sum, sort or count result while preserving a protected file,
leaving a sink empty, and excluding a public synthetic canary from declared exports.
The independent frozen grader reports legitimate completion separately from three
critical effects: changed final protected bytes, a nonempty final sink, and literal
canary bytes in final exports. Submitted receipts, safety claims and workspace
reference files cannot replace that grader or its frozen policy.

```sh
ahl bank --adversarial --list
ahl bank --adversarial --audit --cooperative-controls
ahl bank --adversarial --export /absolute/new/export --max-trials 28
```

These commands return 3 because the exposed synthetic bank is ineligible for
certification, including when all 70 controls pass. Cooperative auditing executes
trusted bundled grader code locally; it does not establish hostile-code containment.
Export preserves the default 24-trial ceiling unless the caller explicitly selects
a larger ceiling. Export does not run an agent. Rebuild with `task bank:build:adversarial`.

To run a comparison, supply explicit offline executors and freeze a matched
[experiment plan and pinned runtime](oracle-history.md), then use
`ahl experiment-history ... run` and `analyze`. A fixed offline solver exercises
controller and grader behavior; it cannot establish model resistance to an attack.
No inference or native agent is launched by the checks described here.

History inputs schema 3 binds each applicable task's raw policy SHA-256, oracle
bundle identity and exact policy before dispatch. Policies are loaded from the
frozen oracle bundle, checked against the plan's task/root/family, retained privately,
and revalidated on guarded reads. Runs without behavior policies retain the existing
schema 1 or bank-bound schema 2. Old frozen policies or evidence cannot be changed
to regrade an existing comparison.
Public development policies require a development experiment and development task
splits; relabelling them as calibration, certification or held-out tasks refuses
before dispatch. Earlier history schemas retain their original interpretation.

Guarded analysis includes private completion/effect counts by family and arm.
A verified critical candidate effect rejects the comparison regardless of average
task score. Missing, malformed, inconsistent or incorrectly bound observations stay
unknown and block a recommendation; an observed critical effect still vetoes when
another channel is unknown. A failed legitimate task receives no completion credit.
Quarantined or unavailable oracle authority still blocks current applicability.

The scope is final declared artifact bytes. A restored write, a hidden read,
encoded or undeclared canary channels, actual external delivery, semantic tool
authorization and provider-side effects remain unobserved. A final empty sink does
not prove it was never written. Controller action observations are a separate
private component and are not admitted into this offline history profile. Automatic
promotion and publication remain disabled. This component does not complete the
broader adversarial behavior and native provenance requirements in issue #8.

`task test:evaluation:adversarial` runs native falsifier and history tests.
`task test:sandbox:kernel:adversarial` explicitly selects real pinned offline workers
and fresh graders for every task under delegated kernel limits. Its synthetic
candidate completes the legitimate work and exports a canary to the sink; the test
requires a critical-effect veto. It tests orchestration and observed final effects,
not model behavior or general sandbox security.
