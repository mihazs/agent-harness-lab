# Inspect a private source journal

[Source contracts](source-contracts.md) · [Analysis methods](analysis.md)

`ahl analyze PROJECT` and `ahl doctor PROJECT` may explicitly inspect a private
source journal with all three arguments:

```sh
ahl analyze PROJECT --source-cache PRIVATE_JOURNAL \
  --source-project-sha256 EXPECTED_PROJECT_HASH --source-scope-sha256 EXPECTED_SCOPE_HASH
ahl doctor PROJECT --json --source-cache PRIVATE_JOURNAL \
  --source-project-sha256 EXPECTED_PROJECT_HASH --source-scope-sha256 EXPECTED_SCOPE_HASH
```

No journal is searched for or opened without these options. The reader validates
the existing journal against the independently supplied source project/scope and
uses one read-only snapshot. It never invokes a connector, reads credentials,
contacts a service, runs a model or upgrades the on-disk journal. Unsupported safe
filesystem capabilities, malformed caches, foreign scope and linked files reject
the requested context. A missing/disappearing journal is explicitly `missing`,
with unknown capture completeness; it is not a successful empty corpus.

The complete inventory reports source fingerprints/kinds, fingerprinted provider
and adapter/parser versions, cached revision/checkpoint counts, declared execution
identity fields, coverage gaps/bounds/limits and graph status counts. Provider,
version and namespace labels from untrusted metadata are not echoed. Every source
and adapter-version fingerprint is shown. Fingerprints remain private metadata, not an
anonymization guarantee. A declared complete scan remains a source assertion.

The report's target-project binding is `unverified`: selecting a scoped source
journal does not establish that its project identity equals the analyzed directory.
Independent roots and historical capture completeness remain unknown, and no
graded outcome or execution authority is granted. Exact cross-source/target binding
still requires the #58 evidence resolver; directory names and matching text cannot
provide it.

Analyze stores context beside its telemetry analysis in private `analysis.json`
and renders a separate section in `report.md`. The context does not add events,
findings or evaluation failures. Doctor adds an optional context check and bounded
JSON/text summaries; it cannot make profile prerequisites pass. Default behavior
without the options stays unchanged. `improve`/`watch` do not implicitly consume
this context. `SourceStore.inventory()` and `sources.context.load(...)` provide
the same offline metadata view, and `task test:sources:context` runs its native
scope, privacy, lifecycle and reporting tests.
