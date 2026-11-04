# One-call improvement: supported paths

Change plans retain their existing schema and operation fields. Both supported
producers also emit an immutable `harness_change_plan` with profile
`ahl-harness-change-plan-v1`; `plan_sha256` identifies its canonical JSON. The
workflow receipt and structural application journal carry that same identity.
The contract records complete before/after file membership, hashes and modes,
including creation, removal and mode-only changes. Declared surface facets flag
executable skill files as requiring a hook contract; activation remains unknown.

Project, configuration, runtime, policy, analysis and evidence revisions are
private hash references. Diagnosis, falsifiers, dependencies and affected controls
are also bound by digest. These references describe producer inputs and confer no
authorization or certification. Empty operations and unresolved executable facets
are explicit; deferred surfaces remain deferred. No raw instructions or credentials
are embedded. Plan documents and identities are excluded from public exports.

Structural application rechecks frozen inventories and current configuration,
preserves unrelated changes, and uses the existing guarded journal and rollback.
If any changed operation has an unresolved executable or hook facet, the entire
repair batch is blocked before journal preparation. Its plan remains reviewable;
no nonexecutable subset is applied and all target bytes and modes are preserved.
Behavioral plans remain review-only with their actual certification prerequisite;
the shared contract adds no application command or new eligible repair.

`ahl improve .` collects scoped evidence, prepares candidate and independent
evaluation sessions, freezes inputs, executes paired tasks and writes a local
`workflow.json`. A favorable behavioral comparison now also writes a versioned
`change-plan.json` listing exact changed paths, hashes/modes and evaluation location.
It remains inconclusive until the applicable certification prerequisites are met.

No manual analyze/manifest/evaluate sequence is required. Select `--agent codex`
or `--agent claude` if both runtimes are available. Authentication remains the
runtime's responsibility; AHL does not inspect credential contents.

For an already prepared controlled Responses environment, persist its settings
and worker references in the [Responses configuration variant](onboarding.md#configure-controlled-responses-once).
Then plain `ahl improve .` resolves that pipeline without a native agent
executable or repeated resource flags. Its broker reads the explicitly selected
private credential only during actual execution; doctor checks metadata only.

When `ahl.json` exists, its profile, executable, scope and limits constrain the run.
Explicit CLI runtime/binary/time options take precedence; configuration never
supplies execution authorization. Offline/unsupported profiles, unintegrated
project banks, stale bank digests, ambiguous runtimes and insufficient trial
budgets stop before inference. A configuration file is optional and is not created
by improve. Configured trials share one ceiling across preparation and both attempts.
The bundled starter bank is not represented as a target-project certification bank.
Only configured harness files enter preparation snapshots; candidates outside that
scope are rejected before evaluation construction. Watch uses the same preflight
and binds its checkpoint to the resolved configuration. Restart watch after changing
configuration; it never silently widens an active run's scope.

## Independently verified format repair

```sh
ahl improve . --repair-contracts
```

This explicit mode performs a narrow deterministic evaluation and application
without a model: remove a UTF-8 BOM before an otherwise valid minimal SKILL.md
header in project-owned skill bundles. The header must have exactly an unquoted
name matching the directory and a simple one-line description. Extended YAML,
executable metadata, mismatched names and unsupported repairs are left untouched.
Only immediate discovered skill roots are repaired; nested examples are preserved.
The body, assets and modes are preserved. The before/after check is recomputed
from the frozen baseline before any target write.

This restores the frontmatter delimiter required by the
[Agent Skills specification](https://github.com/agentskills/agentskills/blob/main/docs/specification.mdx).
It makes no behavioral reliability, native activation or general YAML-validity claim.
It does not rename skills, delete instructions, install hooks or call a model.

A project configuration with `recommendation_only: true` produces a staged plan
and preserves target files. Without that restriction, requesting this explicit
repair authorizes the supported reversible transformation. The existing private
journal records the exact deployment and verifies installed bytes. Restore it with:

```sh
ahl rollback DEPLOYMENT_ID --project /path/to/project
```

Rollback preserves intervening edits by reporting a conflict. The deployment ID
and journal location are included in the repair result. Runtime activation stays
unknown. Plans and receipts contain private paths and must remain local.

If recording the result fails or is interrupted after the journal commits, the
controller reads back its journal and the exact expected inventory before
reporting. A changed or unavailable target requires intervention and keeps the
deployment and rollback identity; it is never reported as an unapplied repair.

Configuration and deadline checks run at the owned journal's mutation checkpoints.
If a check fails or execution is interrupted, exact owned writes are restored when
their current snapshots still match the transaction. Foreign edits are preserved;
an interrupted or conflicting restoration reports `requires_intervention`, remaining
changed paths and the journal location. Those paths may include foreign changes and
do not imply verified change ownership. A rejected transaction cannot be resumed
as an unguarded write. These checks do not provide continuous atomic policy
enforcement against unrelated host writers that ignore the project lock.
An interruption after the commit record, active pointer and exact target snapshots
are all verified is reported as the actual committed result.

## Remaining implementation

This is the first deterministic application slice of #77, not completion of its
behavioral controller. Effective instruction hierarchy (#79), skill lifecycle and
routing utility (#78), native hooks (#80), pinned target-bank integration (#142),
and full executable CLI/skill parity (#143) remain incomplete. The behavioral gate
has not been relaxed. No favorable smoke result or approval becomes certification.
