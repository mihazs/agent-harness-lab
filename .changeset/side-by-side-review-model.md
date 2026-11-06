---
"agent-harness-lab": minor
---

Add `evaluation.side_by_side.build`, a read-only private review model for one baseline/candidate experiment (`ahl-side-by-side-experiment-v1`). It pairs records by task, root, family and repeat, never by list position, and gives each pair an `improved`, `regressed`, `unchanged`, `unknown` or `not_comparable` verdict. Missing and ungraded arms are unknown, not failures. Duplicate, unplanned, invalid and hash- or runtime-mismatched pairs stay visible but are not comparable. Timelines align events only by a shared stable ID or an explicit mapping, and keep only allowlisted metadata, so message bodies, tool arguments and results and reasoning never reach the report. Unknown capture and usage stay unknown, a supplied analysis is copied verbatim and never recomputed, and the report is refused for public export. There is no CLI or HTML viewer yet.
