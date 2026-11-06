---
"agent-harness-lab": patch
---

Give offline evaluation roles and sandboxed graders an empty session keyring. `SandboxBackend.command()` now launches bubblewrap through the same host-side keyring trampoline as hook trials, which joins a new empty session keyring and installs a seccomp filter that fails `add_key`, `request_key` and `keyctl`. A candidate or grader therefore cannot search, describe or read the validator's keys, even by serial number; bubblewrap's namespaces do not isolate keyrings. `/proc/keys` still lists the type and description of keys the validator's uid may view. A kernel without key support proceeds, and any other failure stops before bubblewrap starts. `ahl sandbox-check` then reports `keyring_isolation_unavailable`. Permission-audit runs launch their observer through the trampoline, so the traced exec chain is unchanged. `SandboxBackend.bubblewrap_command()` returns the bubblewrap argv alone for nested launches.
