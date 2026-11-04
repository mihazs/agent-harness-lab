---
"agent-harness-lab": patch
---

Local Git capture now copies packfiles, pack indexes and loose objects into its private view in 1 MiB chunks, and hashes commit binary patches, object read-backs and the trusted Git binary incrementally. Memory no longer grows with the largest pack or patch: on a synthetic 128 MiB packed blob, peak traced Python allocation fell from about 350 MiB to about 2 MiB. Snapshots, summaries, digests, records and journal bytes are byte-identical, and the existing stamp, size, output and binary bounds and their errors are unchanged.
