---
"agent-harness-lab": minor
---

Add an explicit GitHub selection v6 creation-time window for conversation and inline review comments, and record previously present in-window comments missing from a complete recapture as unconfirmed `deleted_or_unobservable` tombstones; a comment listed again after a tombstone becomes a new present revision bound to it. Windows are validated and fingerprinted; failed or truncated captures produce no tombstones; earlier selection versions keep their fingerprints and behavior.
