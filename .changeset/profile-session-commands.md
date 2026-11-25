---
"agent-harness-lab": minor
---

`ahl analyze` and `ahl improve` now count the commands the agent ran in the native sessions they read, reduced to a closed vocabulary (declared scripts and targets, known verification tools, package managers) with no arguments, paths or secrets, counted per session with lineage left unknown. Commands run in several sessions that the harness never names, a package manager the sessions use instead of the one the harness names, and run-shaped rules the user repeats that the harness lacks become findings in the harness profile, with the same citable ids and the same evaluation gates as the static findings. The run receipt also lists which profile findings the candidate harness resolves or introduces, as an observation only.
