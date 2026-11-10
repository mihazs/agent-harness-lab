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
check does not establish resistance to superficial changes. Independent,
private task-specific falsifiers and their provenance remain an open qualification
requirement. Until that authority exists, final-bank audit and qualification are
blocked, and existing qualified revisions cannot establish current eligibility.
Descriptive audits remain available and do not confer certification authority.
