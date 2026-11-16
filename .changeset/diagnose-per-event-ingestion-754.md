---
"agent-harness-lab": patch
---

Remove redundant per-event work from `ahl diagnose` ingestion (part of #751, closes #754): a nine-field order record built and discarded for every event, identity and observation-signature fields read through generators, a source-order tuple built for rows that declare none, an observation check repeated for a case that already qualified, and each timestamp parsed twice. `findings.json` and `report.md` are byte-identical on the 20,000, 80,000 and 240,000-event synthetic corpora and on the shipped examples and Claude contract fixtures. On the 80,000-event corpus the median paired difference was 0.20 s of 2.8 s wall time and 0.21 s of 2.7 s CPU time (15 interleaved pairs, the new code faster in 13 by wall time and in all 15 by CPU time), on a loaded machine; peak memory is unchanged.
