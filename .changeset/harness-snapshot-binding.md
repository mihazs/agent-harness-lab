---
"agent-harness-lab": minor
---

Bind a closed live controller attempt's checked harness input tree to a clean local-Git `HarnessSnapshot` captured earlier in the same process from the same checkout location. The private receipt is `verified` only on an exact match, `mismatch` when the tree differs, and `unknown` for imported, late, relocated, dirty-at-capture or unowned inputs. Effective loading stays unverified, and no journal field, import label or authority is added.
