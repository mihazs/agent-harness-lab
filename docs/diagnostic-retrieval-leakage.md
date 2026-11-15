# Retrieval leakage check for held-out probes

This reference covers `agent_harness_lab.diagnostics.retrieval_leakage.evaluate(index, probes, top_k=None)`,
a library API for issue #14. Incident retrieval is only a fair test of a held-out case when the
local index it searches does not already hold that case. The check reports a held-out probe whose own
declared root, or a near copy of its own text, sits in a [`LexicalIndex`](controlled-experiments.md#contrastive-incident-retrieval).
It runs no model, fits nothing and ranks nothing: it only looks at who is in the index.

## Check an index before evaluating retrieval

Build the index from the incidents permitted for retrieval, then pass every held-out query as a probe:

```python
from agent_harness_lab.diagnostics import retrieval_leakage
from agent_harness_lab.diagnostics.retrieval import LexicalIndex

report = retrieval_leakage.evaluate(
    LexicalIndex(training_incidents),
    [{"root_case_id": "root-1", "split": "heldout", "text": "tool timeout while reading"},
     {"root_case_id": None, "split": "heldout", "text": "unlabeled lineage"}],
    top_k=5)
assert report["status"] != "rejected", report["reasons"]
```

Do not report recall at k or relevance for a held-out probe until the status is no longer `rejected`.

## Input

Each probe is a dict with:

- `root_case_id`: the declared root of the held-out case, or `null` when its lineage is unknown. The key is
  required; a missing key, an empty or non-string value or one over 256 characters is refused.
- `split`: must be `heldout`. Anything else is refused.
- `text`: the nonempty query string.

`top_k` is an optional positive integer, the retrieval depth used to mark which leaked incidents the probe
would actually retrieve. `None` means every lexical match, like `LexicalIndex.query`. A boolean, zero or a
non-integer is refused, as is an empty probe list or an `index` that is not a `LexicalIndex`.

## Report

`diagnostic-retrieval-leakage-v1` lists a finding for each probe and index incident that leaked:

- `same_root`: the incident's `root_id` equals the probe's declared root. Roots are compared exactly.
- `near_duplicate_text`: the incident summary and the probe text meet the corpus
  `word-3gram-jaccard-v1` rule at 800 permille, which also catches a verbatim copy
  (`similarity_permille: 1000`). A probe with an unknown root is still checked for copied text.

A finding gives `probe_root_sha256` (`null` for an unknown root), `incident_id`, its reasons, the
similarity and `retrieved`, whether the incident is among the probe's top `top_k` lexical matches. Any
finding rejects the index, retrieved or not, because the index holds material that belongs to a held-out
case. `leaks` counts findings by kind and how many were retrieved.

The status is `rejected` when any finding exists. Otherwise it is `insufficient_evidence`; there is no
passing status. The reasons also list, when they apply:

- `unknown_root_probes`: probes with an unknown root were counted in `unknown_root_probes` and could not be
  checked against index roots. Unknown lineage is not independence.
- `empty_index`: an empty index has nothing to leak, which proves nothing about retrieval.
- `root_independence_unverified`, `paraphrase_leakage_not_detected` and
  `project_and_time_lineage_not_checked`, always: declared roots are not verified, a paraphrase is not a
  near duplicate, and incident records carry no project or time group to compare.

The report carries root SHA-256 values and incident IDs only, never raw roots, probe text or summaries, and
`private: true` when the index holds a private record. It always states `promotion_authority: false` and
`publication_allowed: false`. It is not a retrieval quality claim, a calibrated probability or a
correctness oracle. The check compares each probe with every index incident, so keep both within the
bounded reviewed corpora the index already expects.
