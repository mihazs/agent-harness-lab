---
"agent-harness-lab": minor
---

`sources.ref_movement.ref_movement` classifies how one ref, or HEAD, moved between two checkpoints of a `local_git/git/v2` journal. The statuses are `unchanged`, `fast_forward`, `non_fast_forward`, `appeared`, `disappeared`, `unobserved` and `unknown`. Ancestry comes only from captured native parent edges, and a history ceiling or shallow boundary keeps a rewrite `unknown`. A non-fast-forward move keeps its cause `unknown`: a force-push, reset, rebase or squash look the same. The function reads only the journal and runs no Git.
