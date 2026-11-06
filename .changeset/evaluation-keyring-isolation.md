---
"agent-harness-lab": patch
---

Give offline evaluation roles and sandboxed graders an empty session keyring. `SandboxBackend.command()` now launches bubblewrap through the same host-side keyring trampoline as hook trials, so a candidate or grader cannot search or read keys held in the validator's session keyring; bubblewrap's namespaces do not isolate keyrings. A kernel without key support proceeds, and any other failure stops before bubblewrap starts. `ahl sandbox-check` then reports `keyring_isolation_unavailable`. Permission-audit runs launch their observer through the trampoline, so the traced exec chain is unchanged. `SandboxBackend.bubblewrap_command()` returns the bubblewrap argv alone for nested launches.
