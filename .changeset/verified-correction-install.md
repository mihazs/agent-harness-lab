---
"agent-harness-lab": minor
---

`ahl improve` installs a verified correction of a static harness contradiction when the paired evaluation shows no gap to measure: the candidate must only replace or remove the lines the finding cites, the profile check must show the finding resolved and nothing introduced, no evaluated task may be lost, and the install review must be consistent. The result stays `evaluation_no_improvement`, and the receipt and report name `install_basis: verified_correction`; a measured install now names `measured_improvement`. Gap findings never use this lane.
