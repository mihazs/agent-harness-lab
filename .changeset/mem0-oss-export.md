---
"agent-harness-lab": minor
---

Add the offline `mem0-oss-export` source for one selected Mem0 OSS v2.2.1 memory and history export. Memories and history rows become hash-only contextual claims: `ADD`/`UPDATE`/`DELETE` events are versions and tombstones, superseded, expired and deleted memories stay in evidence but leave the currently-asserted count, and declared `user_id`/`agent_id`/`run_id` filters exclude other scopes. History for unlisted memories is excluded unless selected. Compatibility stays unverified, the hosted Platform v3 is unsupported, no server or network is contacted, and no memory becomes a task outcome, task root or independent case.
