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
