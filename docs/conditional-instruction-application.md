# Conditional root instruction application

`controller-agents-conditional-apply-v1` is an explicit, limited policy for updating
an existing, nonexecutable UTF-8 root `AGENTS.md`. It uses controlled Responses,
the controller-loaded instruction protocol, a pinned Linux worker image, complete
permission-result capture, and a separately reviewed protected final bank.
Schema 1 and the general certification and adoption APIs keep their conservative
defaults. Configuration, saved reports and passing development tasks do not issue
an application grant.

## Prepare authority before development

First complete [conditional bank review](conditional-bank-review.md), including
the original native control audit. Keep the operator's signing key outside AHL.
The application policy is a separate installed public-key policy with purpose
`conditional_instruction_application`; its SSH namespace is
`ahl.instruction-application.v1`.

Allocate both stable locators before writing the final project configuration:

```sh
ahl application-review --store /private/review allocate /project
ahl bank-review --store /private/review allocate-delegation
```

Use the returned authorization ID and delegation request/signature paths in an
explicit schema 2 configuration. Retain the existing configured Responses runtime
references, `profile: "cooperative"`, `harness_paths: ["AGENTS.md"]`, and
`permission_audit: "permission-results-v1"`. Set `recommendation_only: false` and
provide this additional declaration:

```json
{
  "application": {
    "policy": "controller-agents-conditional-apply-v1",
    "review_store": "/private/review",
    "authorization_id": "ALLOCATED_AUTHORIZATION_ID",
    "bank_case_id": "QUALIFIED_BANK_CASE_ID",
    "delegation_request": "/private/review/delegations/ALLOCATED_ID/request.json",
    "delegation_signature": "/private/review/delegations/ALLOCATED_ID/request.json.sig",
    "method": {
      "experiment_id": "predeclared-final",
      "repeats": 1,
      "seed": 7,
      "max_trials": 20,
      "min_roots": 10,
      "effect_margin": 0.01,
      "family_margin": 0.01,
      "alpha": 0.05,
      "family_weights": {"reviewed-family": 1.0},
      "precision_half_width": null
    }
  }
}
```

The method must match the actual reviewed bank's task families and satisfy the
existing fixed-plan precision calculation. These example counts describe a
declaration, not established root independence or power. The shared trial ceiling
also covers preparation and development. Explicit invocation limits cannot widen
the signed configuration's caps.

`ahl init --config-input ... --write` only writes configuration. It never installs
trust, signs an approval or authorizes target mutation. Doctor remains metadata
only and does not read signing keys or execute this application path.

Prepare and externally sign the closed-input delegation described in the bank
review guide. Bind the full final configuration, selected `AGENTS.md`/`ahl.json`
input snapshot, actual baseline, engine, methodology, history, runtime/settings
and exact evidence source. The initial application policy accepts one signed
source. An explicitly signed empty events file is supported. Future proposal
outputs are admitted through actual same-invocation producer custody.

Then install and prepare the separate application approval:

```sh
ahl application-review --store /private/review install-policy /host/application-policy.json
ahl application-review --store /private/review prepare AUTHORIZATION_ID /project --reviewer PRINCIPAL
```

Sign the returned request under `ahl.instruction-application.v1` outside AHL, then:

```sh
ahl application-review --store /private/review authorize AUTHORIZATION_ID REQUEST.json REQUEST.json.sig
ahl improve /project
```

The approval binds full configuration and reference identities, baseline digest
and mode, bank/delegation, allowed operation, method and limits. Baseline bodies
and provider credentials are not duplicated into authorization records. A locator
or an authenticated approval record alone cannot recreate a live session or grant.

## Execution and result

Plain improve selects and collects the signed evidence once, runs the owned
proposer and evaluator-author stages, and freezes the actual shared change plan.
Known analytic infeasibility stops before model preparation. The final plan is
recomputed from actual frozen baseline/candidate bytes, native grader commands,
oracle and image; it never uses the preparation-only planning identity as evidence.

The controller renews and reserves final access once, runs the complete fixed
schedule, and joins actual requests, native completions, artifact grades and current
authority. It recomputes the existing analysis and critical veto. Only a supported
decision with complete qualified captures can create a one-use grant for that
exact plan and deadline. The existing Journal guards every write and verifies the
resulting inventory, bytes and modes. Mixed or unresolved operations reject the
entire batch.

An applied receipt includes deployment and journal identities for public rollback:

```sh
ahl rollback DEPLOYMENT --project /project
```

Revoked or stale inputs, unknown capture, incomplete coverage, failed critical
checks and insufficient evidence do not authorize a target write. Ambiguous
provider attempts remain spent and poisoned; neither authorization nor final-bank
access can be reset by replaying a report. Cold resume and provider reconciliation
remain unsupported.

The claim is limited to controller instruction injection/transmission, the named
post-preflight process protocol and the observed fixed artifact comparison under
the trusted review's declared assumptions. Physical served-model identity, native
Codex/Claude activation, semantic adherence, pretraining/external exposure and
general scientific independence remain unverified. `certification_eligible`
remains false. All application histories and receipts remain private.

Scope facts retain their existing 128-dispatch and 262144-byte ceilings. Their
authoritative cache uses `canonical-compact-json-v1`; reported receipt bytes count
that exact full state. The representation preserves every field. Global journal
digests and actual provider request bytes retain their existing formats.
