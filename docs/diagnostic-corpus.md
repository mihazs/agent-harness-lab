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
lock gives concurrent claims one winner. Original labels and disagreements remain
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
summaries also fail across protected boundaries even with new root IDs. These
checks detect explicit aliases; semantic independence remains unverified.
Near-duplicate decisions and verified #58 lineage receipts are not yet integrated.

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
No public exporter or corpus-to-classifier adapter is included. The next slices
still need verified grouping, near-duplicate adjudication, a production review
authentication policy, protected evaluation access, class-wise uncertainty plans
and the #74 classifier gate. A reviewed diagnostic annotation remains distinct
from #8's independently reproduced and graded executable regression case.

Run `task test:diagnostics:corpus` for native synthetic controls. The same task is
included in installed wheel/archive verification. No model, network, browser or
optional ML dependency is required.
