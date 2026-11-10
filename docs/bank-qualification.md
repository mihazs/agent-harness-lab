# Bank declarations and certification qualification

[Evaluation foundation](evaluation-foundation.md) · [Content grouping](content-grouping.md)

`ahl bank` validates bounded task metadata, fixture/oracle integrity and the
configured grouping rules. It can list tasks, audit explicitly trusted synthetic
controls and export a descriptive manifest. A structurally valid bank and its
caller-authored review labels do not establish independently reviewed roots,
qualified oracle evidence, untouched evaluation data or reserved final access.

For independently synthetic inputs, a declaration can contain:

```json
{"review_status":"accepted","independence":"reviewed","permission":"approved"}
```

These remain declarations. Validation retains the task metadata and reports
`review_declarations_complete: true` if every task has those three values. It
still reports:

```json
{
  "certification_eligible": false,
  "certification_status": "unverified",
  "certification_reason": "controller_qualification_unavailable"
}
```

The declaration flag is separate from integrity and overlap results. Check
`contents_verified`, `issues` and `content_grouping` for those findings. Missing
review declarations remain visible as issues. Known root/family/project/time
leakage, copied fixtures, changed oracle bytes and invalid inputs retain their
existing checks; lack of certification authority does not skip validation.

A new export retains its fixtures, grading argv, frozen bundles, grouping revision,
artifact allowlists and trial ceilings. Its profile remains `descriptive_smoke`,
with `independence_verified: false`, `bank_certification_status: unverified` and
`automatic_promotion: false`. A legacy positive eligibility boolean is not an
independence issuer. This also applies to a bank entirely labeled certification or
transfer, and to synthetic, public or private source declarations. Public fixtures
are never made secret by copying them into a private path.

The CLI returns its existing noneligible status 3 while still printing the
validation report and completing an explicitly requested descriptive export.
Invalid input remains an error. Example commands, using an authorized local bank:

```sh
ahl bank --manifest PRIVATE_BANK.json --list
ahl bank --manifest PRIVATE_BANK.json --export PRIVATE_EXPORT
ahl bank --starter --audit --cooperative-controls
```

Do not interpret status 3 as a broken grader or failed agent task. Control audit
status, actual task completion and certification eligibility remain separate.
The cooperative audit explicitly trusts authored grader code for a diagnostic run;
it is not sandbox or runtime certification. Controlled regression/corrected banks
retain their separate current development-admission checks and do not gain
certification eligibility from an accepted development revision.

Existing bank files, frozen grader controls, historical exports and sealed run
records are not rewritten. Historical positive metadata labels are not upgraded
into current authority. Development export/replay and current controller-history
validation continue using their existing contracts. No hidden-oracle or effective
runtime claim is added.

## Remaining #8 work

This repairs a false qualification signal; it does not complete #8. There is no
current production bank-certification producer. The production controller now uses
`CertificationLedger` for an explicitly [unqualified managed diagnostic lifecycle](protected-diagnostic-access.md).
Its legacy standalone API and a managed reservation cannot establish independent
curation, untouched data or oracle validity.

The positive path still needs actual independently reviewed source/root evidence,
frozen oracle and falsification evidence, a frozen final candidate, controlled
reserved access, and exposure retirement before final feedback informs another
proposal. It must bind actual producer/consumer evidence through the existing
controller, grouping and ledger; missing authority must stay unknown. IDs,
checksums, operator strings and distinct files do not establish scientific
independence. OS isolation and statistical analysis remain #7/#9 responsibilities.

Other remaining work includes derivation/exposure integration for paraphrases,
scripted replies, rubrics and discovered failures; the complete #123 interaction
boundary; unsupported transient/hidden-read/external-delivery effects in COMP-11;
and reviewed data-only external-evaluation interchange. Existing regression
admission, oracle correction and preservation qualification are bounded foundations,
not a protected certification issuer. No automatic adoption or issue closure
follows from this change.

Run `task test:evaluation:bank-qualification`. The canonical `test:installed` target
includes the same native tests and forwards `PYTHON`, `LAYOUT_DIR` and
`EXPECTED_PACKAGE_ROOT`; an actual import-root assertion checks installed layouts.
All fixtures in these checks are independently synthetic and credential-free.

The descriptive and owned control audits grade a frozen copy of each untouched
fixture (`no_op`). A valid passing result is rejected as `vacuous_success`;
malformed, unrunnable, incomplete or timed-out controls cannot qualify. This
check alone does not establish resistance to superficial changes.

For conditional final-bank qualification, `bank-review intake` also accepts
`--heldout-controls` pointing to a separate owner-only directory's manifest.
The manifest is a JSON object with `schema_version: 1`, a `curator` distinct
from the bank author, nonempty `evidence_refs` in the existing review-reference
format, and `tasks` mapping every task ID to a list of one to sixteen objects
with `path` (relative to the manifest) and `sha256` (the control fixture tree
digest). These are complete workspace snapshots, not executable mutator code.
At most 100 snapshots are accepted. Each must change bytes actually exported to
the grader relative to the materialized original fixture. Copies are frozen in
an owner-only sibling of the bank, never in the grader bundle. Snapshots already
used in another case in the same review store are refused.

The native audit runs every frozen snapshot in the isolated grader path and
requires an explicit negative outcome. Its private receipt binds the exact
control tree, original bank and sandbox execution. An interrupted audit remains
incomplete. The separate trusted reviewer signs a request binding those receipts,
the curator and evidence references, and judgments that curation was independent
and that the task author had not seen the controls before intake. It also binds
the reviewer's `negative_control_semantics: reviewed` judgment that the supplied
negative snapshots represent superficial changes that do not fix the intended
behavior. This is a signed reviewer judgment, not an observation by the grader.
Earlier signed requests without this judgment cannot qualify. The reviewer
must differ from both author and curator. These are conditional, authenticated
human judgments: identity strings, checksums and a private directory do not prove
who saw the inputs. The native execution receipts do not establish prior author
isolation or global non-exposure. Operator access, external exposure and control
semantics outside the selected artifact bytes remain outside observed custody.

Cases without separately frozen controls cannot be audited. Imported banks and
legacy qualified revisions lack observed author-role custody and cannot establish
current eligibility. A controller-managed `WorkflowAccess.author` call may pass
`bank_conversion` with `metadata`, `controls_manifest`, `output`, `review_store`
and `declaration`. Before its author stage retires, the controller requires a
live construction seal, native scope and qualified native permission
boundary, converts the authored held-out tasks while preserving their exact
fixture and grader trees, and intakes the converted bank through a one-use live
handoff. The metadata fields are declarations, not findings. The controller
rejects changed control artifact bytes found in an author input or output; this
byte comparison does not detect transformed or previously disclosed information.
Replay checks the construction ledger, native seal, source assets, frozen bank
and control bytes before conditional signed review or qualification.
The dedicated final-author ledger role accepts held-out tasks only and closes the
workflow to later role dispatch and feedback. Only its own verified construction
is excluded from the development-asset check; matching ordinary development
assets, prior observations and reservations remain disqualifying.

Current positive final-bank review supports the controller-owned
`ahl-json-reference-v1` grader only. Its exact program bytes are checked before
qualification; it runs with Python `-I -S` and reads `result.json` and a nonempty
strict JSON object in `expected.json`. The result must contain that object's fields
with matching types and values. Other grader programs remain unqualified. The
controller records raw file identities and canonical JSON subtree and scalar
value identities for prior oracle files, in addition to whole-bundle identities.
It compares these with values in the final reference: a changed bundle, filename,
JSON key order or surrounding object does not make a matching value fresh.
Repeated ordinary values can cause conservative blocks. Earlier nonempty ledgers without
this per-file coverage, prior opaque grader programs, and observations whose
coverage is unavailable remain unknown and block final-bank qualification.
Matching common oracle data may also
block a bank conservatively. This comparison cannot detect embedded substrings,
semantic transformations or external exposure; it does not establish global
non-exposure or complete role isolation.

When a controlled Responses comparison uses an owned, reviewed-bank reservation,
its private custody record binds the retired reservation to the run and plan.
The controller checks the bank review and reservation at comparison admission and
completion. This record is execution evidence only: external and pretraining
exposure remain unknown, native identity remains unverified, and it grants neither
certification nor adoption authority. A stored record cannot recreate the live
reservation handle.
After sealed readback, the live controller retires that observation once in an
exclusive private record. An interrupted write grants no authority or new live
observation. This is not a certification or promotion decision.

Descriptive audits remain available and do not confer certification or automatic
promotion authority. The controlled author workflow requires its configured model;
the bank audit itself makes no model calls.
