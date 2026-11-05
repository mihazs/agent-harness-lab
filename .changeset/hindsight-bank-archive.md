---
"agent-harness-lab": minor
---

Import one extracted Hindsight v1.1.0 bank transfer archive offline with the `local_file/hindsight-bank-archive/v1` source. Retained documents, extracted facts, causal relations, consolidated observations and curation-archive (invalidated) facts become separate hash-only claims. Each claim keeps its record time apart from its validity window, and its assertion status (current, historical, not yet valid, invalidated or undetermined) is judged at the archive's export time. An archive for a bank other than the selected one is blocked before any document is read. Recall, reflect, retain and any network or service call are refused. Compatibility stays labelled unverified, and no claim becomes a task outcome, agent defect or independence label.
