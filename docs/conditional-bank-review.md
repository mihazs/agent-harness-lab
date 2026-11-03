# Conditional final-bank review

`ahl bank-review` curates a separate bank for a protected diagnostic comparison.
It uses the private regression-review journal, immutable bank revisions and the
configured experiment-history access ledger. Existing development regressions,
owned variants and exposed assets cannot be refreshed into final-bank inputs.

The named protocol is `trusted-lineage-split-and-native-controls-v1`. A successful
qualification means `qualified_under_protocol`. A configured reviewer accepts
source permission, semantic lineage and split-separation judgments. The controller
observes frozen input integrity, actual pinned executor exports, clean-room grader
outcomes and known exposure in the configured stores. These are separate bases.
External access and model pretraining exposure remain `unknown`; this protocol does
not establish global secrecy, mechanically verified independence or certification.

## Operator sequence

Use separate owner-only review and history stores outside all source trees. Supply
a complete version 1 task bank whose proposed tasks use the certification partition,
with approved/reviewed/accepted declarations. Those labels alone remain unqualified.
Every grader bundle must contain five embedded controls in `controls.json`: broken,
partial, complete, alternative_valid and superficial. Fixture and grader hashes,
artifact lists and grader versions remain frozen throughout the operation.

An intake declaration has this form (identifiers and evidence must describe the
actual bank):

```json
{
  "schema_version": 1,
  "author": "curator",
  "history_store": "/absolute/private/history",
  "protocol": "trusted-lineage-split-and-native-controls-v1",
  "judgments": {
    "task-id": {
      "source_permission": "approved",
      "lineage": "reviewed",
      "split_separation": "reviewed",
      "evidence_refs": [{"ref": "local-review-reference", "sha256": "EXACT_SHA256"}]
    }
  },
  "external_exposure": "unknown",
  "pretraining_exposure": "unknown"
}
```

Required unknown permission or lineage judgments reject intake. Install an explicit
external owner-only public-key policy using the existing policy schema, with purpose
`conditional_final_bank_review`. Principals must identify reviewers other than the
intake author. Existing development-review policy and signatures cannot authorize
this purpose. No private key is read by AHL.

```sh
ahl bank-review --store /private/review trust-reviewers --policy /host/public-reviewers.json
ahl bank-review --store /private/review intake --bank /curated/bank.json --declaration /curated/intake.json
ahl bank-review --store /private/review audit --case CASE --cgroup-root /delegated/cgroup --image-root /pinned/image --image-sha256 IMAGE_SHA256 --disk-bytes 67108864
ahl bank-review --store /private/review prepare-review --case CASE --reviewer PRINCIPAL
```

Audit executes actual no-op executor exports followed by isolated artifact grading.
It requires real controller-owned worker completions and cannot use a cooperative
backend or imported success report. The journal records audit intent before work;
interrupted audits remain unresolved and are not automatically retried. The sealed
completed audit is reused during later review without repeating workers.

`prepare-review` returns a canonical request path. The operator signs that exact
file outside AHL using SSH signature namespace `ahl.final-bank-review.v1`, then runs:

```sh
ahl bank-review --store /private/review qualify --case CASE --request REQUEST.json --signature REQUEST.json.sig
ahl bank-review --store /private/review show --case CASE
```

The signature binds the exact bank, task/control inventory through the sealed audit
hash, declaration, image, engine, current exposure epoch, reviewer, policy and
configured history identity. An exposure change makes the version 1 qualification
stale, including unrelated development. Its signed epoch is never rewritten.
The separate delegated protocol below can issue a new conditional qualification
without repeating unchanged completed controls. Changed bank or audit
inputs require new intake. Policy change/removal, retirement, engine changes and
changed evidence invalidate current consumption without rewriting prior events.
Use `bank-review retire --case CASE` to retire a qualification explicitly.

## Protected diagnostic consumption

Export the qualified revision with the existing `ahl bank --manifest BANK --export
OUTPUT` path and freeze a descriptive experiment plan for the actual candidate,
baseline, oracle and audited image. Use the configured history store's existing
`experiment-history reserve-access` and `run --access-reservation` operations.

Reservation and consumption recheck qualification and exposure under the existing
access-ledger lock. Their binding includes the qualification event, immutable bank,
selected materialized tasks, frozen comparison and configured history. Consumption
retires the reservation before copying or dispatch and requires its one-use,
issuing-process handle. Interruption does not refund the set; JSON and copied or
inherited handles cannot supply live authority. A changed configuration, policy or
exposure epoch rejects consumption.

The positive result is a protected **diagnostic** comparison using a qualified bank.
`certification_eligible` stays false: full certification execution and adoption still
require the applicable role/runtime, oracle isolation, current comparison and guarded
application evidence. This command does not turn synthetic smoke coverage into those
prerequisites. All requests, controls, signatures, journals and results stay private;
public benchmark export rejects their qualification metadata.

## Closed input delegation before development

The additive protocol `trusted-closed-input-descendants-and-native-controls-v2`
lets an explicitly trusted reviewer approve a closed input domain before new owned
development occurs. It preserves the original bank revision and strict version 1
epoch guard. Signing later output hashes is not this delegation.

Install a separate owner-only public-key policy whose purpose is
`conditional_final_bank_requalification`. Allocate stable private locators before
freezing project configuration; request content hashes must not create a cycle
through the signed configuration snapshot:

```sh
ahl bank-review --store /private/review trust-delegation-reviewers --policy /host/delegation-reviewers.json
ahl bank-review --store /private/review allocate-delegation
ahl bank-review --store /private/review prepare-delegation --case CASE --reviewer PRINCIPAL --domain DOMAIN.json --runtime RUNTIME.json --request ALLOCATED_REQUEST.json
```

The unsigned request binds the actual qualified bank and completed control audit,
both review policies, initial exposure epoch, history identity, runtime/settings,
and the exact domain. The external reviewer signs it using SSH namespace
`ahl.final-bank-requalification.v1`. These are conditional source/relationship
judgments under the named policy, not mechanically established independence.

The domain profile is `conditional-bank-closed-input-domain-v2`, schema version 2.
It includes the complete declared project input snapshot, a full baseline snapshot,
engine and owned methodology pins, history identity, and one to thirty-two exact sources. Each source declares
`kind`, canonical absolute `selection`, `snapshot_sha256`, `format`, `version` and
`session`. An explicit reviewed empty events file is valid. Missing selection,
autodiscovery, wildcard roots, unknown source versions and unbound imported inputs
reject before collection or a model request. Full snapshots include modes and
directories. `project_inputs` explicitly names profile
`ahl-controlled-agents-project-inputs-v1`, the exact canonical project `root`, and
ordered paths `["AGENTS.md", "ahl.json"]`. It binds root mode, instruction membership
and both files' full bytes/modes. The existing configured harness selection must
be exactly `AGENTS.md`. Unread checkout files, including Git objects, are not
classified as observed inputs and do not consume this snapshot's bounds. Other
harness selections need a separately complete supported profile. Baseline, source
and methodology snapshots retain their existing bounds; unsafe or oversized actual
inputs are unsupported, not partially accepted.
This profile rejects every non-`None` focus input before collection and at the direct
proposal boundary. Legacy workflows retain their existing focus behavior.

The controller verifies the signed request before collection, records the actual
evidence-to-role-view projection, and binds that projection to the live workflow.
Actual Responses requests, tool policy, input bundles, initial state, native worker
completion, exported state and deterministic publication normalization join each
owned construction output. Later outputs may have new bytes and task identifiers;
they must descend entirely from those observed inputs. Refinement feedback also
requires actual owned executor/grader pairs. Its context preserves the stored full
original view, including root and directory modes; the sole permitted addition is
the exact canonical feedback file and mode produced by the existing controller
writer from that native feedback. Unknown parents, source aliases,
pending work, related assets or an unexplained exposure change block renewal.
Provider identity and pretraining history remain unknown.

The Python producer `bank_requalification.begin` returns a live `ClosedDomain`;
`ReviewStore.requalify_bank` requires that domain and the actual selected frozen
candidate/plan, and returns a new process-bound one-use capability.
`HistoryStore.reserve_access(requalification=...)` consumes it through the existing
protected lifecycle. These live objects cannot be restored from their JSON
descriptions or historical proofs. Leaving the workflow scope invalidates them.

Renewal appends a new immutable event under the review-then-access lock order.
Reservation records consumed intent durably before writing the access ledger.
Failure after that intent never refunds the bank or permits replacement issuance;
reconciliation remains unsupported. Current policy, source/evidence and inherited
deadline guards run at publication, including after temporary-file flush and before
atomic replacement. The result remains a qualified-bank protected diagnostic;
this producer does not grant certification or application authority.
