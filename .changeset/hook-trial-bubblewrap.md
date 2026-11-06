---
"agent-harness-lab": minor
---

Run `ahl evaluate hooks` trials under the existing bubblewrap backend by default (`--sandbox bubblewrap`). The run has no network and a cleared environment, and it sees only the system directories, the runtime executable and the trial root, read-only at its own path. Only the trial `home`, `tmp`, `codex-home` and `.ahl-canary` folders are writable, and the user's home and credential stores are not mounted. When bubblewrap or user namespaces are unavailable, or the backend's namespace probe fails, the run is `inconclusive` (`sandbox_unavailable`) and nothing executes; there is no unsandboxed fallback. `host_isolation` is `bubblewrap` only when the shipped backend wrapped the run, `none` for `--sandbox none` or a run that never executed, and `unverified` for any other sandbox object. Results add a `sandbox` block. `SandboxBackend.hook_trial_command` builds the hook trial's mounts.
