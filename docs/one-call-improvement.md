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
After any exactly covered evaluation, `workflow.json` also carries a `decision`
block explaining that outcome; see
[single-run dispositions](decision-receipts.md#default-improve-single-run-dispositions).

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

## Harness surface classification

`agent_harness_lab/surfaces.py` is the only owner of the writable allowlist.
`classify(relative_path, runtime, mode)` returns a `SurfaceClass` with `kind`,
`scope_dir`, `executable`, `writability` and `facets`. `writability` is one of
`adoptable`, `contract_required`, `recommendation_only`, `private_excluded` or
`unsupported`. Candidate scope checks, journal ownership, skill-bundle discovery,
root instruction snapshots and change-plan facets all ask this classifier.

These paths are `adoptable` for every caller: the root `AGENTS.md` and
`CLAUDE.md`, files inside an immediate `.agents/skills/<name>/` or
`.claude/skills/<name>/` bundle, and files under `skills/<name>/` when the project
already owns a top-level `skills/` tree. Nested instruction, rule, agent, command
and nested skill-bundle files are `adoptable` only when the caller has confirmed
checkout containment (see the next section). Any `.git` or `__pycache__` component is
`private_excluded`. A `hook` or `hooks` component outside those bundles is
`contract_required`. Hook registration files are classified as `hook` too:
`.claude/settings.json` (Claude Code) and `.codex/hooks.json` and
`.codex/config.toml` (Codex) are `contract_required`, `.claude/settings.local.json`
is `private_excluded`, and the Git hook managers `lefthook.yml`, `.lefthook.yml`,
`.pre-commit-config.yaml` and `.husky/<file>` are `recommendation_only`.
Workflows, other settings, MCP and plugin manifests are never `adoptable`.
Executable suffixes and execute bits still add the `hook_contract_required` facet,
which keeps such a plan unresolved. The classifier reads no file contents and runs
nothing. A verdict does not show that any runtime loads the file.
`origin="external_repository"` classifies a path in another repository, such as a
selected plugin checkout: every such path is `recommendation_only` unless it is
`private_excluded`.

Each classifier row stands alone. When several rows match a path, the strongest
writability wins, in the order `private_excluded`, `adoptable`,
`contract_required`, `recommendation_only`, `unsupported`. The matching row with
the lowest priority names the kind. A new surface therefore adds a row and
leaves existing rows untouched.

## Staging nested instruction, rule, agent and command files

Containment-gated rows in `surfaces.RULES` make these project files `adoptable`:
nested `CLAUDE.md` (including `.claude/CLAUDE.md`), `AGENTS.md` and
`AGENTS.override.md` at any depth; `.claude/rules/**/*.md`;
`.claude/agents/**/*.md`; `.codex/agents/**/*.toml`; `.claude/commands/**/*.md`;
and files in a nested `<dir>/.claude/skills/<name>/` or
`<dir>/.agents/skills/<name>/` bundle that no `skills/` tree encloses. Each such
row also records `unconfirmed`, its verdict without containment. That is the old
`recommendation_only`, `unsupported` or `contract_required` value, so inventories
and other lexical callers report what they reported before.

`classify(..., in_checkout=...)` takes the caller's observed fact that no
directory between the project root and the path holds a `.git` entry. Only
`candidate_states` and `Journal.owned` pass it. `candidate_states` checks a
staged copy, which holds no foreign repository, so the journal, the only writer,
probes the project again with `promotion.journal.in_checkout`. A file inside a
nested repository or submodule therefore stages but never adopts. A gated row
adopts only a path it names itself and only a non-executable one, so
`.claude/hooks/CLAUDE.md` stays a `contract_required` hook and a script in a
nested bundle stays `contract_required`. Root instructions and root skill
bundles do not depend on this fact, so their verdicts are unchanged.

Both writers check what they write, not only its path. `surfaces.adoption(path,
mode, bytes)` classifies with the file's execute bits, so a changed `0755`
`.claude/rules/x.md` or a suffix-less executable in a nested bundle is
`contract_required` in `candidate_states` and in `Journal.prepare`. It also reads
an agent definition: `surfaces.embeds_hooks` finds a `hooks` word in Claude
frontmatter or a `hooks` key at any depth of a Codex role file, and fails closed
on undecodable bytes, a YAML escape or unparsable TOML. Such an agent needs the
hook contract and is never adopted. An unchanged staged file is never written,
so these checks skip it. The containment probe runs when a promotion is
prepared and again before its first write. Loading a recorded transaction,
previewing it and rolling it back take containment as observed when it was
prepared, so a `.git` created later below the project cannot strand a rollback.
A new promotion of that file is still refused.

`CLAUDE.local.md`, `settings*.json`, `.codex/config.toml`, hooks, workflows,
Codex `.rules`, `.mcp.json`, memory and plugin checkouts
(`origin="external_repository"`) stay non-adoptable. Default discovery is also
unchanged: a snapshot without configured `harness_paths` still stages only root
instructions and root skill bundles, so a same-named file elsewhere never becomes
writable through discovery. A nested file reaches the baseline only when
`ahl.json` `harness_paths` selects it. A candidate can still create a new one. The
configuration accepts any classified path there, but not a `private_excluded` one.

Both proposer prompts (the preparation stage in `workflow.py` and the controlled
proposer in `evaluation/construction.py`) list the allowed kinds from
`surfaces.adoptable_surfaces()`, which joins the `label` every adoptable row must
carry. The prompts still forbid settings, permissions, hooks and credentials. A
change plan marks an operation unresolved when its kind has no adoptable row
(`surfaces.unresolved_kinds()`: hook, workflow, exec policy, config, unsupported)
or when it carries the hook facet. Agent definitions always carry that facet, so
an agent edit stays in `unresolved_operations`. Command and rule edits no longer
do. That plan is advisory; the content check above is what keeps an adopted agent
free of hooks. Staging confers no authority: a behavioral candidate remains inconclusive or
needs certification exactly as before, and only `directly_authorized_adopt_api`
applies a staged nested file, with the journal's stale-baseline, concurrent-edit
and rollback checks.

The module also holds the frozen `CAPABILITIES` table. It has one row per surface,
runtime and documented item, pinned to Claude Code 2.1.317 and Codex 0.166.0
(source `openai/codex@d786ff91`) and retrieved on 2026-11-05. Each row cites its
public documentation URL. Items the research could not confirm are marked
`unverified`, for example the Claude plugin cache subpath and the Codex memories
layout. `verified` means only that the item is documented at the pin.
Classification does not read the table yet.

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
and full executable CLI/skill parity (#143) remain incomplete; the
[receipt parity contract](../skills/harness-self-improvement/references/invocation.md#receipt-parity-with-ahl-improve)
covers the cooperative receipt only. The behavioral gate
has not been relaxed. No favorable smoke result or approval becomes certification.
