# Content exposure in task banks

New in 0.42, `ahl bank --manifest BANK` performs bounded content checks in addition
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
normal exit code is 3. A malformed, ambiguous or over-budget fixture returns the
existing contract-error exit code 2. No command executes an agent by default.

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

The existing 1,000-task and 1,000-file-per-tree limits still apply. Fingerprinting additionally
allows at most 1 MiB per file, 100,000 tokens per fixture and 32 MiB of fixture input
bytes per bank. Output retains at most 128 representative receipts; all overlap
counts and affected-task issues remain visible, with truncation reported. A match
group links each later member to one deterministic representative, rather than
emitting every pair.

Fixture matching is path-sensitive. Arbitrary renaming, paraphrases, partial
copies, near duplicates and hidden semantic relatedness are not completely
covered. Pending receipts have no operator override that can make exposed data
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
