---
"agent-harness-lab": patch
---

An installing `ahl improve` run may repair two defects of the authored evaluation manifest in turn (it was one), and the construction prompt now asks for exactly one broken, one partial and one complete control per task, so a model that writes a second defect after the first repair no longer ends the run `invalid_input`.
