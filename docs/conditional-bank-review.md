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
configured history identity. Unrelated exposure changes can require preparing and
signing a new request; they do not repeat the completed audit. Changed bank or audit
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
