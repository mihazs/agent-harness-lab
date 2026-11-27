---
"agent-harness-lab": patch
---

When an installing run has already measured an attempt without a gain and a later evaluation cannot be built within the repair passes, the run now ends `no_change` on the measured result (recording `later_construction_failed`, and letting the verified-correction lane run) instead of `invalid_input`.
