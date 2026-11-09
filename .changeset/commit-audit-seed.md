---
"agent-harness-lab": minor
---

Let audit sampling plans commit the seed by hash before the window closes. Receipts record the commitment and revealed seed, refuse a mismatched seed or a late commitment, and drop `seed_selection_unverified` only when the check passes.
