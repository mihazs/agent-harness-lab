# Content exposure in task banks

New in 0.42, `ahl bank --manifest BANK` performs content checks in addition
to the existing root/family/project/time and exact-fixture checks. No new account,
network access, inference dependency or automatic import is involved.

A fixture with different byte hashes or declared root IDs can still repeat exposed
content. The validator computes a versioned normalized fixture fingerprint and,
when `task.json` exists, a separate prompt fingerprint. A matching fixture or
prompt adds `content_overlap_unreviewed` and blocks certification eligibility.
Development cases remain inspectable; a matching template with different inputs
is reported as exposure without declaring those tasks the same real incident.

Run the existing commands:

```sh
ahl bank --manifest /path/to/private/bank.json
ahl bank --starter --list
ahl bank --starter --export /path/to/new/private-export
```

The synthetic starter bank remains descriptive and independence-unverified. Its
normal exit code is 3. A malformed or ambiguous fixture returns the existing
contract-error exit code 2. Execution modes also reject inputs beyond their
execution-validation limits. No command executes an agent by default.

Plain bank inspection and `--list` read all declared tasks and fixture/grader
contents without implicit manifest, task-count, identifier-length or tree-size
ceilings. Their report is explicitly `analysis_only`; it cannot grant admission,
execution or certification authority. Controller profiles remain unverified
declarations during inspection. `--audit`, `--export`, qualification and oracle
history keep the existing execution-validation limits and authority checks.

## Fingerprint and receipt contracts

`TextFingerprint` and `GroupingReceipt` live in `sources/grouping.py`, sharing the
existing versioned `RecordRef` contract. Each endpoint identifies its exact source,
native-record identifier and revision through opaque hashes. Bank task references
are local bank declarations; they are not independently authenticated source IDs.

Text algorithm `nfc-word-punctuation-v1` applies Unicode NFC and hashes ordered
word/punctuation tokens. It ignores whitespace between tokens while preserving
negation, identifiers, case, punctuation and token ordering. It does not remove
stopwords or use embeddings. Whitespace can be meaningful inside a string or an
indented program; a match is a conservative exposure candidate, never proof that
two programs or incidents are equivalent.

Fixture algorithm `fixture-path-json-nfc-v1` hashes the relative file paths and
normalized contents, excluding file modes from this secondary check. Valid JSON
object keys are sorted; arrays retain their ordering. Binary files retain exact
SHA-256 identities. Every `.json` fixture must be unambiguous UTF-8 JSON without
duplicate keys or nonfinite numbers. If `task.json` is present, its prompt must be
an explicit nonempty string. Invalid parsing cannot silently disable prompt
visibility. Original fixture/grader hashes and modes remain mandatory integrity
checks; these secondary fingerprints do not replace them.

A version-1 grouping receipt records both exact source revisions and content
fingerprints, the content relation, pending reviewer decision, unresolved
collisions and bounded invalidation history. Computed matches are always
`similar_content` / `heuristic_candidate`; nonmatches are `unlinked`. This version
cannot assert a reviewed/native lineage group, an independent case or an
independent-root count. Serialized receipt labels cannot grant that authority.
Changing an endpoint revision, fingerprint algorithm or supplied invalidation
history changes its receipt identity. Comparisons between different algorithms
are rejected.

The bank result includes `content_grouping.revision_sha256`, binding the algorithm,
exact bank bytes and all inspected fixture/prompt identities. Manifest export
freezes this value as `grouping_revision_sha256`. Source/content/policy changes
produce a new revision; stored experiments keep their old manifest. This does not
automatically rewrite earlier experiments, authenticate their lineage, or execute
the reruns needed after an oracle correction.

## Bounds and remaining work

Fingerprinting has no implicit file, byte, token or overlap-receipt ceiling.
An explicitly supplied fixture byte budget still rejects inputs that exceed it.
Every detected overlap keeps its receipt and affected-task issue. A match group
links each later member to one deterministic representative, rather than emitting
every pair. These observations do not certify independence or authorize execution.

Fixture matching is path-sensitive. The prompt similarity check below is heuristic;
arbitrary renaming, paraphrases, partial copies and hidden semantic relatedness
are not completely covered. Pending receipts have no operator override that can make exposed data
untouched. Reviewed lineage resolution, near-duplicate adjudication and broader
corpus integration remain work under #58; immutable regression admission remains
under #8. Neither issue is completed by lexical matching.

Metadata-only validation (`verify_contents=False`) reads no fixture content,
returns no grouping result and cannot establish certification eligibility.
Private inputs and all derived hashes, receipts, counts and summaries stay private;
local output never authorizes publishing them. No causal or production-precision
claim follows from the independently synthetic controls.

Run `task test:sources:grouping` for native normalization, leakage, invalidation,
parser, resource-bound and export tests. Installed wheel/archive source-test
checks include the same cases.

[Offline source-lineage review](source-lineage-review.md) records operator
annotations over exact cached revisions without upgrading these lexical receipts
or making native-authentication or independent-root claims.

## Suspected root similarity

Bank inspection compares three-token prompt shingles with Jaccard similarity at a
fixed threshold. Code-like identifiers and simple assignment-style variable names are normalized before comparison. A
connected group is a candidate for dependence and its member tasks receive an
`independence_unverified` issue. The report includes nominal and effective root
counts, where each connected candidate group counts once. This is a conservative
planning count, not a verified count of independent incidents. Paraphrases and
other dependencies can be missed; an absent flag does not establish independence.

This check reads prompts locally and does not send them to a service. If any task
has no explicit `task.json` prompt, its report is incomplete and the effective
count and evidence are unavailable. The report flags the incomplete inspection
and still reports candidates found among the prompts that were available. Counts and evidence are private bank-derived
outputs. The lexical candidate is not an authenticated lineage finding.

For a fixed comparison using the whole inspected bank, save the report's
`content_grouping.root_similarity.evidence` as private JSON and put its reported
`evidence_sha256` into the plan's `root_similarity_sha256` before freezing it.
For a selected subset, project the inspected evidence first. The projection omits
unselected task and root identifiers while retaining transitive candidate links:

```python
from agent_harness_lab.evaluation.root_similarity import project_evidence
from agent_harness_lab.sources.schema import digest

# report is the private bank-inspection result; plan_tasks is the selected
# sequence of (task_id, root_id) pairs from the frozen plan.
full = report['content_grouping']['root_similarity']['evidence']
evidence = project_evidence(full, plan_tasks)
plan['root_similarity_sha256'] = digest(evidence)
# Save evidence as private JSON and freeze the plan before collecting outcomes.
```

The evidence must cover the plan's exact selected task/root mapping. The plan hash
includes that pin. Supply both the evidence and current bank manifest:

```sh
ahl experiment --spec /path/to/plan.json --preflight \
  --root-similarity /path/to/private-evidence.json --root-bank /path/to/bank.json
```

Supply the same two options when analyzing recorded trials. The current bank is
reinspected; missing, changed, or mismatched evidence blocks the calculation.
Candidate roots are combined for uncertainty calculations by adding their weights
within each component. Primary, family, and required preservation endpoints use
those grouped weights while retaining their original weighted means. The inverse
sample-size solver is unavailable when candidates are present because resizing
requires new frozen evidence. Sequential plans and power simulation do not accept
this evidence. Legacy plans without a pin retain their existing behavior and do
not make this additional check.
