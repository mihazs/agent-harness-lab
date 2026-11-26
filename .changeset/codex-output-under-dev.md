---
"agent-harness-lab": patch
---

`ahl improve --agent codex` refuses an `--output` under `/dev` (such as `/dev/shm`) up front: Codex's sandbox mounts a private `/dev`, so its sessions could not read the run's inputs and the run ended inconclusive after the proposer found nothing to read.
