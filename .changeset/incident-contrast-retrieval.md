---
"agent-harness-lab": minor
---

Add `LexicalIndex.contrast` and `ahl incident-search --hypothesis LABEL [--control-label LABEL]`, an `incident-contrast-v1` report that groups lexical matches into supporting, contradicting, control and unresolved (`unknown`/`ambiguous`) cases, each bounded separately by `--top-k` so close supporting matches cannot hide counterexamples. Results carry root hashes instead of raw root IDs, labels, evidence references and provenance, and withhold summaries from private records. The report counts distinct and shared roots and unmatched hypothesis-labeled incidents, and always states `diagnosis: hypothesis_only`, `causal_claim: false` and a non-estimable prevalence.
