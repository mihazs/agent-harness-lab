# Private diagnostic annotation corpus

The first `diagnostic-corpus-v1` API supplies a local review queue and immutable
incident revisions for issue #73. It does not choose or calibrate a classifier,
verify a historical task outcome, admit a regression task, or publish results.
It uses the existing owner-only `PrivateJournal` rather than a second database.

`agent_harness_lab.diagnostics.corpus.CorpusStore` exposes:

| Operation | Behavior |
| --- | --- |
| `enqueue(record)` | Freeze a bounded incident and its exact evidence fragments; start pending |
| `packet(case_id, reviewer)` | Return the frozen review packet without prior labels, predicted summary, author or candidate identity |
| `update(case_id, expected_event_sha256, kind, ...)` | Claim, submit, adjudicate or record exposure using the exact current event revision |
| `snapshot()` | List current queue metadata and its revision, without label/evidence bodies |
| `export_development(expected_revision_sha256)` | Return eligible fitting/development annotations; protected labels are excluded |

`record` is a schema-version-one object with opaque incident/root/family/project
and time-group IDs, partition, source kind/reference/revision, permission/license,
summary, separately marked predicted summary, bounded evidence fragments,
capture completeness, ambiguity, selection reason, rubric digest, author and an
optional `supersedes` case ID. Each evidence fragment has an ID, content and exact
UTF-8 SHA-256. Inputs are limited to 128 KiB and 16 fragments of at most 4096 bytes.
Unknown capture may be `null`; it cannot support a known outcome annotation.
File callers can use the existing `private_journal.record()` bounded JSON reader,
which rejects duplicate keys and invalid numeric constants, before `enqueue`.

An annotation stores terminal outcome, recovery, symptom labels, mechanism labels,
verification provenance, exact evidence references and a reason separately.
Terminal outcomes are `task_failure`, `successful`, `external_failure`,
`requirements_changed` or `unknown`. A recovered intermediate error can therefore
have terminal outcome `successful` and recovery `recovered`. Neither keywords,
negative wording nor the predicted summary determine these fields. Missing
verification, incomplete capture, unapproved evidence or absent references cannot
support a known outcome. Mechanism annotations are declared interpretations,
without causal authority.

The lifecycle is pending → claimed → submitted → claimed → reviewed → adjudicated.
The second submission uses a different declared reviewer identity; the author
cannot review their own incident, and adjudication uses a third declared identity.
Claim/submission bindings and exact event hashes reject stale clients. The store
lock gives concurrent claims one winner. Declared identities reject surrounding
whitespace and compare case-insensitively for reviewer separation. Original labels and disagreements remain
in append-only history after adjudication; an unknown final annotation remains
unresolved and is excluded from development export.

These are **operator-attested identities**, not authenticated independent humans.
Packets provide convenience blinding, not an isolation boundary against the local
store owner, who can inspect files. Agreement does not create a gold label. Every
receipt/export explicitly retains unverified reviewer/root independence,
`gold_label: false`, and no classifier-selection or task-outcome-oracle authority.
Model suggestions cannot become gold labels through this API.

Fitting, calibration, threshold, development, validation and certification are
distinct partition roles. Current heads cannot reuse root, incident or exact
source-revision identities across partitions. Whitespace/case-normalized copied
summaries also fail across protected boundaries even with new root IDs. A full
summary that exactly matches a normalized evidence fragment in another partition
also blocks export, regardless of which appears first. Shared evidence fragments
alone do not trigger this check. These checks detect explicit aliases only;
semantic independence remains unverified. Existing stores or exported manifests
with newly detected cross-field matches can fail validation after this change.
The development export omits protected-only records, so its consumer cannot
reconstruct or independently check aliases involving omitted protected data; the
complete private store is required for that check.

Near duplicates across effective partitions need a recorded decision. The frozen
`word-3gram-jaccard-v1` rule scores each incident's summary against the other's
summary and evidence fragments (word 3-gram Jaccard, case-folded) and flags a
pair at 0.80 or above. `CorpusStore.decide_near_duplicate(first, second, decision,
reviewer, reason)` stores one immutable, operator-attested decision per pair of
incident revisions, `distinct_incident` or `same_lineage`, by a declared
reviewer who is neither incident's author. Export fails without a decision for a
flagged pair and always fails on `same_lineage`; such a pair has to be rebuilt in a
fresh store with consistent lineage. Decisions bind exact case IDs, so a requeue
invalidates them, and they are part of the export revision and travel in the
manifest's `near_duplicate_decisions`, which `development_export` re-checks for
the exported pairs. The rule is lexical only: paraphrases below the threshold are
not detected, and a decision is not evidence of independence. Verified #58 lineage
receipts are not yet integrated.

An `exposed` event retires a protected item to effective development use. This
retirement persists through all source/rubric requeues. Export preserves the
original frozen record alongside its effective partition rather than rewriting
history. Source/rubric changes require an explicit `supersedes` revision and fresh
pending reviews; existing agreement is never carried forward. Competing requeues
are rejected atomically. Root/family/project/time, declared partition and source
privacy identities cannot be relabeled by requeue. Export revisions bind every immutable journal head,
the existing whole-application evaluator engine identity and the named
`whitespace-casefold-v1` preprocessing rule. Stale export revisions fail.
Re-importing an exported record starts pending; review history is not imported as
authority.

All storage and exports remain private, including synthetic-source declarations.
The existing benchmark exporter rejects all canonical corpus outputs even if
their publication flag and public provenance are forged.
No public exporter is included. The next slices still need verified grouping,
a production review authentication policy, a certification-role evaluation and
the #74 classifier gate's use of real models. A
reviewed diagnostic annotation remains distinct from #8's independently
reproduced and graded executable regression case.

## Benchmark input profile

Each development export carries `manifest_sha256` over all of its other fields.
`development_export(manifest)` checks that digest, the frozen export authority
fields and limitations, each record's case ID against its frozen incident, the
annotation against that incident, export eligibility and fitting/development
separation. The digest detects changed or truncated files. Anyone who can edit
the file can recompute it, so it is not an authenticity signature; the partition,
eligibility and provenance checks run regardless.

`ahl benchmark export.json --profile diagnostic-corpus --method baseline` reads such
a manifest. It keeps the corpus assignment, mapping `fitting` to the benchmark's
`train` split and `development` to `development`, and never re-splits. A manifest
that names a calibration, threshold, validation or certification partition fails
validation. An exposed item is the exception. Exposure retires it to development
even when its declared partition was protected, and such a development row must
carry the export's `retirement` reference to the exposure event. The reference
must name the row's own final event or, through `supersedes`, an earlier
revision. A row whose effective partition differs from its declared partition
without that reference is rejected. The reference is a consistency check, not
authentication: someone who edits the file can fabricate one. The report's
`partitions` field therefore counts each split's rows by declared partition,
with `retired` and `retired_from_protected` totals, so retired protected items
are always disclosed. Protected items that were not exposed are never exported. With no
calibration partition, threshold selection has no roots and every prediction
abstains. Brier scores and per-root groups are still reported. `--triage-policy`
is refused for this profile because a development export has no held-out roots.

The classifier text is the frozen human `summary`. The predicted summary is
never read. The binary label is `true` for `task_failure` and `false` for
`successful`. `external_failure` and `requirements_changed` remain unlabeled
rather than becoming negatives, and `outcome_counts` reports them separately.
In corpus reports, split and project metrics name these rows
`unlabeled_outcomes`, not `unknown`, because the corpus `unknown` outcome is
never exported. The adapter profile keeps its `unknown` field.
`groups` reports rows, declared roots, repeated-root rows, the most rows for one
root and roots with conflicting labels for each split. The `splits` metrics are
row-weighted (`metric_weighting.splits: row`), as in the adapter profile: labeled
counts, coverage, precision, recall and Brier count every row. Only
`accepted_roots` and `root_accuracy_lower_95` group rows by root. `root_weighted`
reports a Brier score that weights each labeled root once, averaging its own rows
first. Roots with conflicting labels are excluded and counted in
`conflicting_label_roots_excluded`, not averaged. Root independence remains
`unverified`.

Every report states `label_provenance_counts` for `adjudicated`, `single_review`
and `adapter_smoke`, plus `reviewed_annotations` and `gold_labels: 0`. Corpus
rows are `adjudicated` operator-attested declarations. The current store exports
no single-review labels, so that count is zero. Rows from the default
`adapter-smoke` JSONL profile, including the synthetic classifier fixture, are
`adapter_smoke`. They never count as reviewed annotations, and a JSONL row that
claims another provenance is rejected. A corpus report has
`qualification: descriptive_only`, `certification: not_issued`,
`publication_allowed: false` and `classifier_selection_allowed: false`. It uses
the private `diagnostic-corpus-benchmark-v1` profile, which the benchmark exporter
rejects even with forged public provenance.

This profile shows that local adjudicated declarations can be scored under the
corpus's own split and root groups without exposing protected items. It is not
evidence of classifier accuracy. Real reviewed gold labels need authenticated,
independent human reviewers and an agreed gold-label policy, both still open in
#73. The same applies to certification-role evaluation and the #74 acceptance gate.

## Class-wise annotation plan

No fixed corpus size is sufficient. `ahl annotation-plan SPEC.json OBSERVED.json`
compares a frozen per-class plan with caller-supplied independent-root counts.
`SPEC.json` is `{"schema_version":1,"classes":{NAME:{"half_width":0.1,"min_roots":20}}}`;
`OBSERVED.json` is `{NAME:{"precision":ROOTS,"recall":ROOTS}}`. For each class and metric
the report gives the roots needed for the worst-case (proportion 0.5) Wilson 95%
interval to reach the declared half-width (or `min_roots`, if larger), the additional
roots still required and `sufficient` or `inconclusive`. Sparse classes and unreported
counts stay `inconclusive`; they are never zero evidence or success. The spec digest
is reported so a plan can be frozen before outcomes are seen, but the order of
preregistration and root independence are not verified. The report is
`planning_only`: it estimates annotation need, not precision or recall, and grants no
certification, classifier-selection or publication authority.

## Protected validation access

The validation partition has no label export. `CorpusStore.evaluate_protected` is the
only path that uses those labels for an evaluator, and it releases none: it returns a
report from the frozen v5 [multi-class triage policy](diagnostic-triage.md#multi-class-policy-schema-v2)
and retires the evaluated cases. Calibration and threshold partitions are not reachable
this way, and the certification partition stays reserved for a genuine qualification
issuer ([protected diagnostic access](protected-diagnostic-access.md) states the same
limit for executable comparisons).

1. Fit and freeze a model from `export_development`. `protected_inputs()` returns
   `plan_sha256`, the corpus revision and, for every untouched validation case, its
   `case_id` and human `summary`. It carries no label, root ID, author or predicted
   summary. Unreviewed cases are listed too, so the list shows no review progress.
2. Score those texts outside the store, one score per policy class.
3. `evaluate_protected(plan_sha256, policy, model_sha256, scores)` takes a policy whose
   classes are exactly the four corpus terminal outcomes, whose `model_sha256` matches,
   and whose declared fit, calibration and threshold root lists are disjoint from each
   other and from the evaluated roots. `scores` covers exactly the planned case IDs.
   Any change to the store since `protected_inputs()` (a new case, review, requeue or
   decision) makes the plan stale.

The triage policy evaluation runs before anything is written, so an invalid policy, score
or model digest retires nothing and can be retried. A returned result means each labeled
case was appended an `exposed` event naming the access ID, so it now counts as
development data and a later `export_development` carries it with its retirement
reference. A validation label therefore supports one evaluation; a rejected result is
still a consumed one.

Only a root whose every current case has an eligible, known adjudication and one agreed
outcome is labeled. Other roots (unreviewed, `unknown`, incomplete capture, unapproved
evidence or conflicting outcomes) are scored as unlabeled roots, stay protected and
count in `unlabeled_cases`. The returned `evaluation` is the policy report: aggregate
class metrics, confusion counts, abstentions and hashed root decisions. Its `status` is
`rejected` or `insufficient_evidence`; nothing issues acceptance.

This is an exposure record, not custody. The store owner can read the journal files, a
model owner can refit after seeing the report, and a crash during retirement can leave
some cases retired without a returned result. Operator-attested labels, unverified
reviewer and root independence, `gold_label: false` and no classifier-selection or
publication authority all carry over. The access runs offline and needs no model, network
or optional ML dependency.

Run `task test:diagnostics:corpus` for native synthetic controls, including the
benchmark profile. The same task is
included in installed wheel/archive verification. No model, network, browser or
optional ML dependency is required.
