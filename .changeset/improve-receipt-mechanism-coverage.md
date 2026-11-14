---
"agent-harness-lab": minor
---

`ahl improve` now admits the mechanisms and falsifiers its proposer writes as unverified records and adds a private `mechanism_coverage` field to its receipt, with `mechanism-admission-<attempt>.json` and `mechanism-coverage.json`/`.md` beside it. The proposer is told the entry format and may cite the analysis brief's own mechanism IDs. Every admitted mechanism stays `proposed` and `open` after a tie or an accepted patch, refused entries are counted with their reasons, and capture stays `unknown`. Nothing here diagnoses, evaluates or validates a mechanism.
