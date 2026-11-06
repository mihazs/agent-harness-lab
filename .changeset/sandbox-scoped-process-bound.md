---
"agent-harness-lab": patch
---

Stop the sandbox process bound from failing every run on a busy host. Without a cgroup root, `SandboxBackend.run()` set `RLIMIT_NPROC` on the launcher, which counts every process of the invoking real UID, so bubblewrap's first clone failed with `EAGAIN` once the user ran more than 32 tasks. The cap is now set on the sandbox's first process inside its own user namespace (Linux 5.14+), while bubblewrap holds it on `--block-fd`, so it counts only the sandbox's processes. When no sandbox-scoped bound is available, the run continues and its attestation reports `process_bound` as not enforced, and `ahl sandbox-check` lists `no sandbox process bound`. A probe whose clone fails with `EAGAIN` now reports `process_limit_exhausted` instead of `namespace_probe_failed`.
