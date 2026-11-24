---
"agent-harness-lab": patch
---

The install review now runs before the confirmation repeats. When it finds rules that no task measured, the proposer removes exactly those and the remaining change is judged, confirmed and installed, instead of the whole confirmed improvement being refused; a change it finds contradictory or test-specific ends the run without spending the repeats. A consistent early review is reused at install.
