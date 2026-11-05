---
"agent-harness-lab": patch
---

Reads that only hash, size or compare a file now hold one chunk at a time instead of the whole file. New `regular_sha256`, `regular_size` and `regular_equal` helpers apply the same regular-file, symlink, single-link and byte-limit checks as `read_regular`. They are used for lineage store snapshots and the cache re-check, the normalized export readback, the closed source pin, correction, revision and oracle byte accounting, and the smaller manifest and receipt hashes. The benchmark input digest and the sandbox binary digest use `hashlib.file_digest`. Derivation snapshot pins feed `repr` into the hash in pieces, so they no longer build an escaped copy of every file. Digests, pins, error messages, limits and ordering are unchanged.
