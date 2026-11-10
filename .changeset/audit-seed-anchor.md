---
"agent-harness-lab": minor
---

Anchor the audit sampling seed commitment time in a hash-chain log proof verified offline (#417). A `seed_commitment` may carry an `anchor` whose first entry covers exactly the commitment hash and whose chain ends in a caller-supplied trusted head observed strictly before the window end. Only a verified anchor drops `commitment_time_unauthenticated`; unanchored or unverifiable receipts keep it, and anchors over another hash, broken chains, untrusted heads and late times are refused.
