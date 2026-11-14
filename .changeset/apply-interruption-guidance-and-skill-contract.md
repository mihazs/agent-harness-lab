---
"agent-harness-lab": patch
---

An interrupted `ahl apply` no longer ends in a Python traceback: a review says it wrote nothing, and an interrupted authorization says to repeat the same command and names any unfinished deployment to finish with `ahl promotion --action recover`; both exit 1. The bundled self-improvement skill now documents the review and apply step (the user confirms the exact plan hash; approval is not certification) and requires `ahl` 0.198.0, the first release with `ahl apply`. New tests interrupt a repair run while journaling and before the target write and show that the saved plan is then reviewed and applied exactly once, and that review and apply reach the same target bytes in the source, wheel and archive layouts.
