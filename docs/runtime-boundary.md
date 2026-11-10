# Restricted-process runtime facts

The optional `permission-results-v1` controlled workflow can qualify the named
`ahl-linux-restricted-process-v1` startup protocol. This is a private, live
controller decision about every admitted post-preflight dispatch. It does not
qualify general role isolation, scientific eligibility, native or served-model
identity, effective model instructions, or automatic adoption.

The supported boundary trusts the controller process, Linux kernel, host
bubblewrap and the selected pinned image's setup tools. Arbitrary code inside the
trusted controller, a compromised host/toolchain, side channels and semantic
authorization are outside this protocol. Unknown observations stay unknown.

The controller freezes its actual selected input mounts, image, inherited
environment, user identity, namespaces, writable-volume sizes, resource ceilings
and deadline. The exact monitored startup verifier reads bounded effective facts
before executing role code: readonly mounts and input/root identities, private
workspace identity, tmpfs capacities, distinct namespaces, one-entry user/group
maps, zero capabilities, no-new-privileges, active seccomp, loopback-only network
devices, an exact environment digest and inherited resource limits. The fixed
environment includes `PWD=/work`; environment values and host paths are not
included in the observation record.

Bubblewrap's `--dev` uses an intermediate devpts setup namespace. The final
worker retains the controller's numeric nonroot UID/GID, mapped to zero in that
immediate parent, rather than host root. The protocol requires this exact mapping
and zero effective/permitted/bounding/ambient capabilities. See the
[bubblewrap implementation](https://github.com/containers/bubblewrap/blob/v0.13.1/bubblewrap.c#L3030).
Other mappings cannot qualify this version of the protocol.

The verifier writes at most 4 KiB to a fresh CLOEXEC pipe, closes its writer and
passes only the read descriptor through the existing startup socket. It closes
both local descriptors before untrusted execution. The controller requires one
owned, read-only FIFO, a bounded complete frame and EOF. Ordinary role stdout is
never parsed as a boundary observation. The original storage READY handshake is
preserved.

Qualification also requires the existing exact five-exec monitor chain, private
nonce-bound monitor channel and one-use native completion, image/input integrity,
actual cgroup control readbacks and terminal whole-tree cleanup. Expected and
observed facts are frozen in the existing owned dispatch registry and projected
through its opaque fact into the current process-bound Scope. JSON attestations,
fake backends and copied or retired contexts cannot supply this authority.
Source fanout shares the original native completion.

An audited controlled workflow cannot retain a favorable recommendation when any
admitted dispatch lacks protocol qualification. The result identifies the first
unqualified role/ordinal and a fixed predicate name, without raw readbacks.
Unrequested/cooperative paths retain unknown protocol qualification. Full
mandatory certification remains unverified, even for a qualified protocol.

Permission-result semantics are unchanged: project-role EACCES/EPERM/EROFS
violations remain violations; deliberate capability probes retain separate
attribution. Setup telemetry stays unverified/incomplete as applicable. ENOENT,
hidden access attempts, semantic intent, transient effects and loaded executable
identity remain outside the claim. Readbacks describe trusted startup, not every
later role state.

Use `task test:runtime:boundary` for ordinary transport/report controls and
`task test:runtime:boundary:kernel` for synthetic Responses transport with real
pinned workers, all four roles, source fanout and missing-startup-fact rejection.
These checks use no provider, credentials or native-agent session.

## Cooperative native trial environment

The Codex and Claude trial runner and its version probe pass only `PATH`, `HOME`,
`TMPDIR`, `TERM`, `TERMINFO`, `COLORTERM`, `LANG`, `LANGUAGE`, standard `LC_*`
locale variables, plus runtime authentication variables. Codex receives
`CODEX_API_KEY`; its temporary `CODEX_HOME` still does not include saved logins.
Claude receives `CLAUDE_CONFIG_DIR`, `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_API_KEY`
and `ANTHROPIC_AUTH_TOKEN`; the existing `HOME` continues to support saved login.
When Claude explicitly enables Bedrock, Vertex or Foundry in the parent, the runner
also passes the selected provider's connection and authentication variables:

- Bedrock: `CLAUDE_CODE_USE_BEDROCK`, `AWS_PROFILE`, `AWS_REGION`,
  `AWS_DEFAULT_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
  `AWS_SESSION_TOKEN`, `AWS_BEARER_TOKEN_BEDROCK`, `AWS_CONFIG_FILE`,
  `AWS_SHARED_CREDENTIALS_FILE`, `AWS_WEB_IDENTITY_TOKEN_FILE`, `AWS_ROLE_ARN`.
- Vertex: `CLAUDE_CODE_USE_VERTEX`, `GOOGLE_APPLICATION_CREDENTIALS`,
  `ANTHROPIC_VERTEX_PROJECT_ID`, `ANTHROPIC_VERTEX_BASE_URL`, `CLOUD_ML_REGION`.
- Foundry: `CLAUDE_CODE_USE_FOUNDRY`, `ANTHROPIC_FOUNDRY_API_KEY`,
  `ANTHROPIC_FOUNDRY_AUTH_TOKEN`, `ANTHROPIC_FOUNDRY_RESOURCE`,
  `ANTHROPIC_FOUNDRY_BASE_URL`, `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`,
  `AZURE_CLIENT_SECRET`, `AZURE_CONFIG_DIR`.

Claude also receives `ANTHROPIC_BASE_URL`, `ANTHROPIC_MODEL`, and
`ANTHROPIC_DEFAULT_OPUS_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL`, and
`ANTHROPIC_DEFAULT_HAIKU_MODEL`. Advanced provider settings,
custom credential chains and model-specific regional overrides may need
`--inherit-env`, which passes the full parent environment and can expose unrelated
secrets to the agent.
Each launched trial records that opt-in, sorted environment names and a SHA-256 of
the sorted, compact JSON environment; it records no environment values. A second
comparison digest replaces the private run root in values with `<run>` so separate
runs can check parity without discarding the exact digest. The hash
can reveal low-entropy values by guessing, so keep results private. The receipt
records the requested subprocess environment, not later agent or hook changes.
These processes retain access to host files and network; this does not establish
isolation and does not change unknown boundary qualification.
