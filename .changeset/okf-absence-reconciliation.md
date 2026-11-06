---
"agent-harness-lab": minor
---

Add the opt-in `sources.okf.ReconcilingOKFExport` reader. Through `Registry.ingest` it compares an ai-memory OKF bundle with the journal's committed page revisions. It records one unconfirmed `tombstone` for each page path missing from the bundle, and a new revision bound to the latest one when a page reappears or reverts to bytes committed earlier, so each path's latest journal revision matches the bundle. Absence is reported as not deletion evidence, renames are not inferred, the checkpoint stays the bundle fingerprint, and `ahl sources import` keeps the default reader.
