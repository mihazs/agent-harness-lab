---
"agent-harness-lab": minor
---

Add an offline Git/GitHub commit join over cached checkpoints. It keys on the declared GitHub repository fingerprint plus the exact full lowercase SHA, reports sources without a shared repository identity as unlinked, force-pushed commits as stale and missing identity as unknown, and emits only a private candidate receipt with no outcome or authority.
