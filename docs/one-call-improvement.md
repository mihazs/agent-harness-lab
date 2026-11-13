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

A project owner can create a `.ahl-read-only` marker in the project root to
freeze AHL-driven changes. The marker takes precedence over flags and settings:
`improve` (including skill invocation and `--repair-contracts`), `watch`,
`init --write`, skill installation, promotion, recovery and rollback refuse to write. Remove the
marker to permit those operations again. `analyze` and the static audit remain
available; analysis output and its state directory must be outside the project.
A marker also covers descendant projects; their outputs must be outside the
marked directory. Any marker type, including a dangling symlink, activates the
freeze. CLI output, export, cache and store destinations inside the marked
directory are refused, including through symlinks; shared journal and JSON
writers enforce the same guard. Select an external directory for private state.

The marker is a cooperative guard, not a filesystem sandbox. Keep it in place
before starting an operation: another process can change it between a check and
a filesystem operation. If a freeze appears during a multi-file transaction,
recovery may require removing the marker and manually resolving the recorded
conflict. If installation is interrupted by a freeze, the error names the backup
location so the owner can recover the files after removing the marker. Agent
processes already running and other tools are not controlled.

`ahl improve .` collects scoped evidence, prepares candidate and independent
evaluation sessions, freezes inputs, executes paired tasks and writes a local
`workflow.json`. A favorable behavioral comparison now also writes a versioned
`change-plan.json` listing exact changed paths, hashes/modes and evaluation location.
It remains inconclusive until the applicable certification prerequisites are met.
After any exactly covered evaluation, `workflow.json` also carries a `decision`
block explaining that outcome; see
[single-run dispositions](decision-receipts.md#default-improve-single-run-dispositions).

No manual analyze/manifest/evaluate sequence is required. Authentication remains
the runtime's responsibility; AHL does not inspect credential contents.

### Runtime selection without `ahl.json`

With `--agent auto` (the default) and no configured runtime, AHL applies this
fixed order and records the outcome as `runtime_selection` in the preflight plan
and `invocation.json`:

1. An explicit `--agent`, then a trusted project `ahl.json` runtime.
2. The runtime remembered in the user-level configuration, when its executable is
   on `PATH` (`user_remembered`). A remembered runtime that is not installed is
   ignored.
3. Exactly one supported executable on `PATH`: use it (`single_installed`).
4. Several installed and the invoking host is a supported runtime: use that
   runtime (`invoking_host`). The host is read from the presence of `CLAUDECODE`
   or a `CODEX_*` session variable, never from credential contents. If both or
   neither are present the host is unknown.
5. Otherwise stop with `select_one_runtime_with_agent` (`ask_once`) before any
   spend. The plan's `choice` lists the runtimes; ask the user once, then rerun
   the same command with `--agent <choice>`.

The policy never changes a model, provider or permission. Choosing the host's
runtime does not run the host session itself:
AHL starts its own isolated child process of that executable, so the host runtime
is acceptable for the cooperative `codex` and `claude` profiles. Choose an
isolated runtime explicitly (a configured `responses` pipeline) when the
evaluation must not share the host's tooling or account.

### Remember the runtime choice

Remembering is opt-in. Add `--remember-runtime` to a run with an explicit
`--agent codex` or `--agent claude`; the choice is stored once that run passes
preflight. Without the flag nothing is written, and the flag without an explicit
`--agent` is an input error (exit 2).

```sh
ahl improve . --agent codex --remember-runtime
ahl config show     # {"path": ..., "runtime": "codex", "reason": null}
ahl config clear    # deletes the file
```

The file is a versioned `{schema_version, runtime}` document of at most 1 KiB at
`$XDG_CONFIG_HOME/agent-harness-lab/user-config.json` (`~/.config` when unset), or
`$AHL_STATE_HOME/user-config.json` when that run-state override is set. It is
written atomically with owner-only permissions, holds no executable path,
credential or token, and is never read from or written to a project, so a project
file cannot override it. A corrupt, oversized, non-regular or not owner-only file
is ignored (`ahl config show` names the `reason`). The remembered runtime only
chooses among installed runtimes: it never overrides `--agent` or a project
`ahl.json` and grants no execution authority.

To evaluate from sessions already recorded, add `--history`; see
[improve from past sessions](improve-from-past-sessions.md).

An explicit invocation is the authorization to run this bounded default
workflow. `execution_authority: false` in a blocked plan only states that
nothing was dispatched.

For an already prepared controlled Responses environment, the [Responses configuration variant](onboarding.md#configure-controlled-responses-once)
persists its settings and worker references. Plain `ahl improve .` resolves that
pipeline without a native agent executable or repeated resource flags. Its broker reads the explicitly selected
private credential only during actual execution; doctor checks metadata only.

When `ahl.json` exists, its profile, executable, scope and limits constrain the run.
Explicit CLI runtime/binary/time options take precedence; configuration never
supplies execution authorization. Offline/unsupported profiles, unintegrated
project banks, invalid or unavailable banks, stale bank digests, ambiguous runtimes and insufficient trial
budgets stop before inference, each with its own `reason` and a matching one-step
`remediation` in `workflow.json` (the generic text is only a fallback). A configuration file is optional and is not created
by improve. Configured trials share one ceiling across preparation and both attempts.
Once preflight passes and before any model dispatch, the CLI writes one `ahl: preview` line to
stderr with the runtime, harness scope, the time and trial ceilings, the authorization statement and
`model_cost="unknown..."`: model usage is billed by the runtime provider, AHL does not measure tokens or
money, and the ceilings bound time and trials, not spend. A blocked run prints no preview. Skill
invocations also record the same `cost_preview` in `invocation.json`.
On completion, improve writes its structured receipt to stdout and a concise
outcome summary to stderr. The summary includes the reported changed-file count,
artifact directory when available, a next step, and elapsed time. Once a
controller budget clock starts, it also reports measured time spent and time
remaining; otherwise those values are unknown. Trial spend and cost remain
unknown because reservations are not execution or billing observations. Artifact
paths are private local information; keep the output within the authorized environment.
The bundled starter bank is not represented as a target-project certification bank.
Only configured harness files enter preparation snapshots; candidates outside that
scope are rejected before evaluation construction. Watch uses the same preflight
and binds its checkpoint to the resolved configuration. A watch restart is required after configuration changes; it never silently widens
an active run's scope.

## Profile and platform support

The table describes ordinary behavioral `ahl improve` with an `ahl.json` file;
preflight can still block a row for an invalid bank, missing capability or budget.

| Profile and runtime | Platform | Current behavior |
| --- | --- | --- |
| `offline`, `none` | Supported POSIX filesystem APIs | Doctor and configuration preview work; behavioral improvement blocks before inference. |
| `cooperative`, `codex` or `claude` | Supported POSIX filesystem APIs | A trusted absolute executable is required in configuration. The runtime is cooperative, and authentication remains unverified. |
| `cooperative`, `responses` | Linux and supported POSIX filesystem APIs | Requires explicitly configured controlled-worker references and passing capability checks. A configured nonstarter project bank is not supported on this path. |
| `isolated` | Supported POSIX filesystem APIs | Doctor reports the isolation certification prerequisites as blocked; configured behavioral improvement stops at preflight. |
| Any | Other platforms or missing required filesystem APIs | Safe configuration I/O is unavailable; use a supported host. |

Supported configuration I/O requires descriptor-relative, no-follow POSIX
filesystem operations. See `ahl doctor . --json` for local readiness checks;
its output does not verify authentication or certify isolation. The explicit
`--repair-contracts` mode below has a separate deterministic, model-free contract.

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

Vendored and generated trees have no `.git` of their own, so containment alone
would treat them as project folders. A path with a directory component in
`surfaces.VENDORED_DIRECTORIES` (`node_modules`, `.venv`, `venv`, `vendor`,
`bower_components`, `jspm_packages`, `site-packages`, `__pypackages__`, `.tox`,
`.nox`) keeps its kind but is at most `unsupported`, for every caller and origin,
and carries the `vendored` facet. `private_excluded` still wins. The folder
right below a `skills/` or `agents/` directory of a skill or agent path is a name
the project chose, so a skill bundle called `vendor` or `venv` keeps its verdict,
while that bundle's own `node_modules` or `.venv` stays vendored. Skill discovery
skips those dependency trees, so they are never staged as candidate files.
Generated output is found through the project's ignore rules instead:
`classify(..., ignored=...)` takes the caller's observed fact that those rules
exclude the path, and evaluates it only for a nested path that would otherwise
be `adoptable`. Root harness folders (`.claude/`, `.agents/`, `.codex/` and a
root `skills/` tree) are never probed, so a project that keeps `.claude/` local
through ignore rules still adopts its root rules and skills. `Journal.owned`
passes `promotion.journal.ignored`, which runs a read-only
`git check-ignore --no-index` on the path's folder, with fsmonitor disabled,
the user's global excludes file unset and no inherited `GIT_*` variables, so a
hook's `GIT_DIR` cannot answer for it. Only directory-level rules decide: a
file-name pattern such as `CLAUDE.md` or `AGENTS.md` keeps instruction files
local and does not make `app/CLAUDE.md` foreign. For an ignored folder it then
runs a read-only `git ls-files --error-unmatch` on the path, so a tracked,
force-added file is not reported as ignored. A project with no `.git` at or
above it has no ignore rules, so git is never started for it. A missing git
binary, a timeout or any answer other than "not ignored" fails closed. An ignored folder's `CLAUDE.md`, `.claude/rules` or
skill bundle therefore stages but never adopts. A root file has no enclosing
folder and is not probed.

Each classifier row stands alone. When several rows match a path, the strongest
writability wins, in the order `private_excluded`, `adoptable`,
`contract_required`, `recommendation_only`, `unsupported`. The matching row with
the lowest priority names the kind. A new surface therefore adds a row and
leaves existing rows untouched.

`surfaces.matrix()` is the generic surface matrix: one row per `SURFACES` entry
with each runtime's writability and route (`adopt`, `contract`, `recommendation`
or `excluded`), computed from these rows for one synthetic path per runtime. An
adoptable path whose edits carry the hook facet, such as an agent definition,
routes to `contract`. Change plans derive the surface part of `deferred_surfaces`
from `surfaces.deferred(resolved)`: every surface the plan does not resolve,
except memory, which both runtimes exclude. The behavioral candidate resolves the
surfaces some runtime routes to `adopt`; `--repair-contracts` resolves only skill
headers. The [surface reference](../skills/harness-self-improvement/references/surfaces.md)
shows the matrix.

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
the frontmatter of an agent definition, a command, a path rule and a `SKILL.md`:
`surfaces.embeds_hooks` finds a `hooks`, `permissionMode`, `mcpServers` or
`allowed-tools` word (any case) in Claude frontmatter, or a `hooks` key at any
depth of a Codex role file, and fails closed on undecodable bytes, a YAML escape
or unparsable TOML. Such a file needs the hook contract and is never adopted.

A command or a `SKILL.md` can also run shell commands from its body. The
[Claude Code skills docs](https://code.claude.com/docs/en/skills) (pin
`2.1.317`) document two forms of dynamic context injection: an inline
`` !`command` `` whose `!` starts a line or follows whitespace, and a fenced
block opened with ` ```! `. Claude Code runs the command when the user invokes
the command or skill, before Claude sees the content. `surfaces.embeds_shell`
therefore routes such a file to the hook contract. It scans the whole file,
frontmatter included, because substitution runs over the original file. Forms
the docs do not confirm fail closed:

- a `!` right before a backtick after any other character, as in
  `` KEY=!`cmd` ``, or in prose that quotes `` `!` ``;
- blanks between the `!` and the backtick;
- a fence of four or more backticks, a tilde fence, and blanks between the fence
  and its `!`;
- a fence anywhere in the text, not only at the start of a line: indented, in a
  list item or blockquote, mid-line as in `` Env: ```!cmd``` ``, or after a bare
  CR, because Claude Code's block matcher has no line anchor;
- undecodable bytes.

A plain fenced code block is not excluded: an inline form inside it still counts.
The docs never say that a plain fence stops substitution, so AHL does not assume
it. A `SKILL.md` is checked under every skill root, because one bundle can load
in both runtimes. Agent definitions, path rules, instruction files and other
bundle files are not checked for this syntax, because the docs do not say Claude
Code runs it there. A body that mentions a command in a code span, or a plain
fenced block without the marker, stays adoptable.

An unchanged staged file is never written, so these checks skip it. The containment
probe runs when a promotion is prepared and again before its first write,
together with the ignore-rule probe. Loading a recorded transaction, previewing
it and rolling it back take containment and ignore rules as observed when it was
prepared, so a `.git` or `.gitignore` created later below the project cannot
strand a rollback. A new promotion of that file is still refused, and so is
rolling back that rollback, which re-applies the promotion's content and is
therefore probed and checked like a new promotion. Along a chain of rollbacks,
each one that restores the original content is taken as observed, and each one
that re-applies the promotion's content is probed and checked.

`CLAUDE.local.md`, `settings*.json`, `.codex/config.toml`, hooks, workflows,
Codex `.rules`, `.mcp.json`, memory and plugin checkouts
(`origin="external_repository"`) stay non-adoptable. Only a hook or workflow unit
that passes its contract below reaches the journal, and only when a caller passes it. Default discovery is also
unchanged: a snapshot without configured `harness_paths` still stages only root
instructions and root skill bundles, so a same-named file elsewhere never becomes
writable through discovery. A nested file reaches the baseline only when
`ahl.json` `harness_paths` selects it. A candidate can create a new nested
instruction or rule file only inside the governing scope its instruction plan
declares (see the next section). The configuration accepts any classified path
there, but not a `private_excluded` one.

Both proposer prompts (the preparation stage in `workflow.py` and the controlled
proposer in `evaluation/construction.py`) list the allowed kinds from
`surfaces.adoptable_surfaces()`, which joins the `label` every adoptable row must
carry. The prompts still forbid settings, permissions, hooks and credentials. A
change plan marks an operation unresolved when its kind has no adoptable row
(`surfaces.unresolved_kinds()`: hook, workflow, exec policy, config, unsupported)
or when it carries the hook facet. Agent definitions always carry that facet, so
an agent edit stays in `unresolved_operations`. Command and rule edits no longer
do. That plan is advisory; the content check above is what keeps an adopted agent,
command, rule or skill free of hooks, permission grants and MCP servers. Staging confers no authority: a behavioral candidate remains inconclusive or
needs certification exactly as before, and only `directly_authorized_adopt_api`
applies a staged nested file, with the journal's stale-baseline, concurrent-edit
and rollback checks.

## Hook contract

`hooks_contract.py` defines `hook_contract_v1`. A hook operation is one
deployment unit: the `hooks` key delta of exactly one project configuration file
(Claude `.claude/settings.json`, Codex `.codex/hooks.json` or the `[hooks]` table
of `.codex/config.toml`) plus the checkout scripts its handlers reference.
`hooks_contract.stage(project, config, hooks, scripts)` builds the journal
snapshots without writing the project. `promotion.journal.key_edit` rewrites only
the top-level `hooks` key: every other JSON member, TOML table and the comments
that introduce the next table keep their bytes, and an ambiguous layout (a
duplicate key, root `hooks.*` dotted keys, an inline `hooks` table) is refused.

`hooks_contract.evaluate(project, before, after)` is static and executes nothing.
A unit is `blocked` when the event is not in the pinned list, a regex matcher
does not compile with Python `re`, a handler is not a `command` naming a regular,
non-symlink checkout script through `"$CLAUDE_PROJECT_DIR"/` (a cwd-relative path
is `script_anchor_unverified`), a handler sets `commandWindows`, `command_windows` or
`shell`, a referenced script is group- or world-writable, its timeout is not set
within 1 to 600 seconds, a member is unreferenced, or the config bytes are not
exactly `key_edit` of the before bytes. Python `re` stands in for the runtimes' JavaScript and Rust engines,
so `matcher_check` stays `python_re_proxy_runtime_regex_unverified`. Script modes
are recorded. A network or credential word in the command line is a heuristic
finding that makes the unit `recommendation_only`. `settings.local.json`,
managed layers and plugin `hooks/hooks.json` are never written. AHL never writes
Codex trust state: `[hooks.state]` stays byte-identical, a value carrying it is
refused, and a Codex unit reports `trust_review: requires_user_trust_review`,
which the change plan lists in its `hook_contracts` rows and `required_reviews`.
A hook in agent frontmatter, or a root `hooks` key in `SKILL.md` frontmatter,
makes that file `contract_required` too; a skill description that only mentions
hooks stays adoptable. Without a frontmatter key edit such a unit stays blocked.

Codex hook changes stay recommendation-only. At the pinned
`openai/codex@d786ff91` source, Codex runs a project hook command through the
login shell in the session working directory and gives it no project-root
variable (see [Codex hook command anchor](source-contracts.md#codex-hook-command-anchor)).
The working directory can be a nested folder or a linked worktree of the
project, so no command form names one checkout script. Every Codex unit is
therefore blocked with `codex_anchor_undocumented`, including one that only
removes or empties handlers, and a command handler also gets
`script_anchor_unverified` or `command_not_a_checkout_script`. AHL can propose
a Codex hook change but never apply one.

`Journal.prepare(..., hook_contract=verdict)` is the explicit authorization. The
journal evaluates the unit again and admits its `contract_required` hook members
only when both verdicts agree and the unit passed; admission declared in metadata
is refused. The admission binds the sha256 and mode of every member snapshot and
referenced checkout script, so a record whose snapshots differ is refused, and
`Journal.execute` evaluates a promotion again before writing. The unit applies as one deployment. A crash between the script and
config writes resumes with `Journal.execute` or returns every member to its before
state with `Journal.recover`, and a foreign edit to any member is a conflict that
writes nothing. Rollback admits exactly the members its reverted deployment
admitted, and only when its snapshots are the exact reversal of that committed
deployment's. A change plan resolves a `contract_required` operation only through a
passed contract (`hook_contracts`), and `ahl improve . --repair-contracts` treats
`hooks` as resolved, dropping it from its deferred surfaces, only when every hook
unit in the run passed.
No CLI path stages a hook unit yet. A passed contract is not behavioral evidence.
Behavior comes only from `ahl evaluate hooks`, which runs the unit beside a nonce
canary inside a private trial workspace with no credentials in its environment,
sandboxed by default by the bubblewrap backend without network or host home
([hook canary validation](../skills/harness-evaluation/references/execution.md#hook-canary-validation)).

## Workflow contract

`workflow_contract_v1` applies the same executable contract to Claude Code
workflow scripts. A unit is one or more direct `*.js` children of a checkout
`.claude/workflows/` folder, the `contract_required` `workflow` kind.
`hooks_contract.stage_workflows(project, {path: (bytes, mode)})` builds the
journal snapshots without writing the project, and `evaluate` returns a workflow
verdict when every member is a workflow script. A transaction that mixes hook and
workflow files is no single unit and stays blocked: a workflow member of a hook
transaction is `workflow_member_outside_hook_contract`, even when a hook command
names it. `contracts_for` returns one hook unit and one workflow unit for such a
change.

The [static workflow lint](source-contracts.md#static-claude-workflow-script-lint)
of every candidate is a hard gate. A unit is `blocked` when `meta` is not the
first literal statement (`meta_not_first_literal`), when the script uses
`import(`, `Date.now()`, `Math.random()`, a no-argument `new Date()` or a static
`import`, when the lint is inconclusive (`workflow_lint_inconclusive`), when a
member is removed, is group- or world-writable or lies in a nested repository.
The document records `lint: static_lint_not_runtime_validation`. A passed unit is
admitted by `Journal.prepare(..., hook_contract=verdict)` exactly as a hook unit
is, and `surfaces.CONTRACT_KINDS` names the two kinds an admission can cover. A
workflow unit never lifts the deferred `hooks` surface.

Analysis never runs a workflow script. `hooks_contract.trial_request(verdict,
side, project, trial, args)` only describes one native run of a passed unit: the
caller's runner executes it inside `trial/workspace`, an I13 trial copy that must
hold the side's bytes (`baseline` before, `candidate` after) and must lie outside
the project, so no project `.claude/workflows/` on the cwd-to-root path can load.
Its `cwd` is the members' owner folder inside the workspace (the path before
`.claude/workflows/`), so their folder is on that path; a unit whose members sit
in sibling folders that no single cwd loads gets no request. The request adds `CLAUDE_CODE_WORKFLOW_MAX_CONCURRENT_AGENTS` capped at 2 (gated
at Claude Code >=2.1.269 in `CAPABILITIES`) and a canonical JSON args fixture of
at most 2048 bytes. AHL pins no CLI form that starts a workflow, so the request
says `invocation: caller_runner_unverified`.

`hooks_contract.outcome(verdict, before, after, runs=None, project=None)` labels
the unit and attaches a git-format `patch` with mode changes and missing final
newlines marked. A blocked unit is `blocked`. Without runs the result is
`recommendation_only`. Judging runs needs `project`. Runs must be a `passed` or
`failed` grade for both a baseline and a candidate request made for this unit with
the same args fixture and within the cap, each with an absolute owner-folder `cwd`
outside the project and in a different trial workspace; anything else is
`inconclusive`. Only such runs give
`improved` (the baseline failed and the candidate passed) or `not_improved`. The
label is never adoption authority. Codex has no workflow loader at the pinned
source, which every workflow document records.

The module also holds the frozen `CAPABILITIES` table. It has one row per surface,
runtime and documented item, pinned to Claude Code 2.1.317 and Codex 0.166.0
(source `openai/codex@d786ff91`) and retrieved on 2026-11-05. Each row cites its
public documentation URL. Items the research could not confirm are marked
`unverified`, for example the Claude plugin cache subpath and the Codex memories
layout. `verified` means only that the item is documented at the pin.
Classification does not read the table yet.

## Scoped instruction rewrites and pruning

`agent_harness_lab/sources/instruction_validation.py` checks instruction and
path-rule candidates against their effective context before any evaluation. It
runs the instruction resolver (`sources/instructions.py`) and the rule resolver
(`sources/claude_rules.py`) again on a baseline closure and a candidate closure.
Each closure holds the project's in-checkout instruction and rule files, read
through the read-only harness inventory, with the staged copy laid over them.
For Claude Code it also holds the regular in-checkout files that those files
import with `@path`, read without crossing a link or a nested repository, so a
missing import means the target is really missing. `CLAUDE.local.md` and other
private files stay out. Operations compare the two closures, so an edit to a
project file that the baseline does not stage carries that file's real
baseline hash. Nothing is executed, and every result keeps
`observed_loading` and `effective` at `unknown`.

A proposal that changes a nested instruction file or any rule file must add an
`instruction_plan` to `proposal.json`:

```json
{"schema_version": 1,
 "profile": {"schema_version": 1, "runtime": "claude-code", "version": "2.1.317", "task_cwd": "src"},
 "task_paths": ["src/app/view.ts"],
 "required": [{"id": "tests", "text": "Always run the unit tests."}],
 "shadowing_changes": [{"change": "add", "shadow": "src/AGENTS.override.md", "shadowed": "src/AGENTS.md"}],
 "removed_task_paths": []}
```

A plan may also justify each changed file with an `operations` list. Each entry
names the `path`, a `decision` (`add`, `update`, `remove` or `consolidate`), the
`mechanism_id` and `evidence_refs` (at least one label) it cites, the
`expected_change`, the required behaviour ids it `preserved` and its
`falsifier`. Once a plan lists any, every changed instruction or rule file needs
exactly one entry whose decision matches the validator's own classification
(`operation_unjustified`, `justification_for_unchanged_path`,
`justification_decision_mismatch`), and an entry that cites no evidence, names an
undeclared required id or carries multi-line text makes the plan invalid. The
report's `decisions` list records each justification with hashes for the text
fields, never the text. A plan without `operations` leaves `decisions` null:
the justification is a declaration, not evidence that the mechanism is real, and
it grants no adoption authority.

`profile` uses the instruction resolver's profile schema, and its runtime must be
the trial runtime (`responses` validates as `codex`, because the controlled
executor loads the `AGENTS.md` chain). Root-only edits without a plan keep their
previous path. Both proposer prompts state these rules. The validator blocks a
candidate in these cases:

- It edits an instruction file outside the governing scope. The governing scope
  is the deepest directory on the task path that holds an applicable instruction
  file, plus the task directory itself (`outside_governing_scope`).
- It edits a rule that matches no declared task path (`rule_matches_no_task_path`).
- It edits a file the runtime does not load as an instruction or rule
  (`not_a_runtime_instruction_surface`).
- It adds an import cycle (`import_cycle`), a missing import (`missing_import`)
  or another unresolved import (`import_unresolved`).
- It changes which file shadows which, such as Codex override precedence or
  Claude `AGENTS.md` shadowing, without the plan declaring that change
  (`undeclared_shadowing_change`).
- A rule's `paths` edit stops matching a declared task path that is not listed in
  `removed_task_paths` (`rule_paths_dropped_task_path`).
- A required text leaves the active context of a task path. It can move to an
  inactive file (`required_text_left_active_context`) or disappear
  (`required_text_removed`). A newly required text must also become active
  (`required_text_not_active`).
- The staged baseline no longer matches the project (`stale_baseline`).
- The plan is missing or invalid, its runtime is not the trial runtime, its task
  directory does not exist, or either closure fails to resolve.

`one_call.check_scope(args, proposed, original)` enforces the same scope. It
rejects a changed instruction or rule file outside the validated scope. Without a
validated scope it rejects any changed nested file the frozen baseline does not
already hold.

The validated scope starts from the plan's `task_cwd` and `task_paths`, which
the proposer declares, so the first validation report marks it `scope.source:
"proposer_declared_plan"`. Once the evaluation manifest is frozen and validated,
and before any baseline or candidate trial runs, `instruction_validation.bind`
checks the plan against the directories the evaluated tasks run in. A task runs
at the checkout root `.` unless it declares a `cwd` and the trial runner honors
it. Only the controlled Responses executor does: it passes the task `cwd` to the
trial, which loads the root-to-cwd `AGENTS.md` chain. The command-line agents,
the configured bank on those agents and the fixed comparison run every trial at
the workspace root, so `bind` refuses a manifest that declares a task `cwd` there.
The proposer does not author these directories: the configured bank, the fixed
comparison or the separate evaluation author does. A plan whose `task_cwd` is not
one of them is refused (`task_cwd_not_evaluated`). A task path must lie below the
plan's own `task_cwd` and below an evaluated directory, or the plan is refused
(`task_path_not_evaluated`).

Nothing the proposer does not author declares a task path, so a task path stays
a proposer declaration (`scope.task_paths_source: "proposer_declared"`). The
validation report lists in `scope.task_path_scope` what only the declared task
paths make governing: an instruction directory below the `task_cwd` chain, such
as an on-demand Claude `docs/CLAUDE.md`, and every path rule that does not load
at launch. The bound scope drops those, and an edit to one of them is refused
(`task_path_scope_unbound`). Any refusal stops the run as
`instruction_validation_blocked` before those trials, except a project that exceeds
the inventory entry bound: validation reads the project's instruction and rule files
through the harness inventory, so that run stops as `inventory_entry_bound_exceeded`
with `inventory_remedy` (the largest top-level directories, names and counts only;
see [audit a long-lived checkout](audit-long-lived-checkout.md)). The inventory is
read after the proposal call, so that call has run; no trial has. The rewritten
`instruction-validation-<attempt>.json` records `scope.source:
"evaluation_manifest_task_cwds"` and `scope.task_cwds`, and `check_scope`
enforces that bound scope from then on. `current` reports `scope_unbound` for a
document that was never bound, so a recommendation never rests on a
proposer-declared scope alone. For example, a proposer cannot declare
`task_cwd: "docs"` to make `docs/CLAUDE.md` governing when every evaluated task
runs in `src` or at the root. With every task at the root, declaring the task
path `docs/guide.md` does not make `docs/CLAUDE.md` or a `docs/**` rule writable
either. A plan for root tasks must declare `task_cwd: "."`, and only the root
instructions and the rules that load at launch are in its bound scope; a Codex
`app/AGENTS.md`, which loads only from `app`, stays outside it.

A shorter file is not automatically better. The report gives active-context bytes
(`utf8-bytes-v1`) for both versions as a secondary measurement only. A reduction
earns `credited_reduction_bytes` only after subtracting the lines that moved out
of the active context into another file, and `selects_winner` is always `false`.
Every measurement covers the task's own launch context at `task_cwd` as well as
each declared task path, so declaring a task path never takes the evaluated
task's own context out of the lost lines, the removal or the credited bytes.
When a removed file's active lines all stay active in the same contexts, the
operation is `consolidate`. Otherwise it is `remove`. Any active line that leaves
the context sets `removal.disposition` to `requires_behavioral_controls`. After
evaluation, `removal_disposition` reads the trial records. It returns `rejected`
when a candidate regression control fails. It returns `inconclusive` when
regression controls are missing, a baseline control fails, or the
`workspace_surfaces` of a trial workspace do not hold each version's changed
files exactly, including the absence of a removed file. It returns `recommended`
only when all of those hold, and `certification` stays `required`. When a
candidate removes active guidance and the disposition is not `recommended`, the
workflow records no recommendation and no change plan. Its reason is
`instruction_removal_<disposition>`. Controlled runs record no
`workspace_surfaces`, so a removal there stays `inconclusive`. A fixed-bank
run (`--experiment-request`) hands no trial records to this gate, so a removal
there is `inconclusive` with reason `trial_records_unavailable`, and the
workflow withholds the comparison's `review_candidate` recommendation. Before
recording a recommendation on either path, the workflow runs the validation
again. A changed project instruction file or a modified evaluated candidate
invalidates the recommendation, and the project is left untouched. The validator
never reads evaluation manifests, graders or held-out inputs.

When the workflow records a behavioral candidate, `change-plan.json` also holds an
`instruction_review` made by `instruction_validation.review`. It lists each changed
instruction or rule file with its scope directory, `add`, `update`, `remove` or
`consolidate` decision, baseline and candidate hashes, the plan's justification (mechanism,
evidence labels, preserved ids, text hashes) and the removal gate's evaluation disposition,
which every operation except an `add` carries. Its `blocked`, `staged`, `recommended` and `applied` lists stay
separate, `applied` is always empty, and it carries a `sha256` over its content, never the text of a
file. It grants no adoption authority; the change-plan and the journal remain the only
application path. When the same candidate also changes a skill or hook path, `composed` names those
paths and marks an `interaction_evaluation` as `required`, because no per-surface result covers
the combination.

## Validating staged agent and command definitions

`sources/agents_validation.py` checks every created, updated or removed Claude
`.claude/agents/**/*.md`, Codex `.codex/agents/**/*.toml` and Claude
`.claude/commands/**/*.md` file in a candidate before any evaluation. It parses
the staged bytes again with the agent and command inventory parsers (see
[declared custom agent definitions](source-contracts.md#declared-custom-agent-definitions)
and [commands](source-contracts.md#commands-skill-roots-and-codex-custom-prompts)),
loads nothing and gives each file one verdict:

- `blocked`: the runtime would skip, reject or ignore part of the file. Claude
  agents need a leading `---` fence on line 1 (a leading blank line or BOM
  blocks), a valid `name` and a `description`, valid YAML in the reader's
  subset, only documented fields and no field gated above the pinned version.
  Codex roles need `name`, `description` and `developer_instructions`, valid
  TOML and only fields in the pinned `deny_unknown_fields` set. A role without a
  `description` is blocked, although Codex can fill it from a lower layer. A
  command must not use frontmatter the runtime ignores (`name`, `paths`) and its
  frontmatter must parse. Two staged agents with the same name in one
  `.claude/agents` or `.codex/agents` tree, or two commands with the same id,
  are `duplicate_name`. Any `hooks` key in an agent or command
  (`hooks_need_hook_contract`) is routed to the hook contract.
- `recommendation_only`: the change adds, removes or alters a permission
  field, namely Claude `tools`, `disallowedTools`, `permissionMode` or
  `mcpServers`, or command `allowed-tools` as a string, flow list or block
  list. For a Codex role every field outside a fixed allowlist of identity,
  instruction and model-tuning fields (`name`, `description`,
  `developer_instructions`, `model`, `model_reasoning_effort`, `personality`
  and similar) is a permission field, because fields such as `tools`,
  `permissions`, `web_search`, `notify`, `features`, `openai_base_url` or
  `model_providers` enable tools, run programs or redirect model traffic. A new
  agent that declares one counts as a change. No plan declaration authorizes
  such a change yet. A field value the reader reduces, such as a nested
  `mcpServers` mapping, counts as changed whenever the file bytes differ.
- `no_effect`: the change cannot alter runtime behaviour, so it is never counted
  as an improvement. A Codex role update that changes nothing but
  `sandbox_mode`, `approval_policy`, `model_provider` or `mcp_servers` gets
  `no_override_effect`. A command whose id matches a staged
  `.claude/skills/<dir>/` skill gets `shadowed_by_skill` and the recommendation
  `migrate_to_skill` (#78). A no-override field in a new role or next to an
  effective change, or one left unchanged, only sets the `no_override_effect`
  flag and leaves the file adoptable.
- `adoptable`: none of the above. Removing a definition is always adoptable.

The candidate takes the strictest file verdict, except that `no_effect` files
next to an adoptable file or any other changed path leave the candidate
adoptable, since it still changes runtime behaviour. `ahl improve` stops before
freezing or evaluating a non-adoptable candidate: `blocked` gives status
`blocked` with reason `definition_validation_failed`, `recommendation_only`
gives `inconclusive` with `definition_change_recommendation_only`, and
`no_effect` gives `no_change` with `definition_change_has_no_effect`. The result
carries a `definition_review` document with fixed labels and paths only, never
file text, and `adoption_authority: false`. The staging check runs first: a changed Claude
file whose frontmatter names `hooks`, `permissionMode`, `mcpServers` or
`allowed-tools`, or a Codex role with a `hooks` key, needs the hook contract, so
`ahl improve` and `workflow.adopt` refuse it with an error before this review. `workflow.adopt` runs the same
review and refuses anything but `adoptable`. Duplicate and skill-name checks see
only the staged tree, so a same-name file outside the configured `harness_paths`
is not checked. Behavioral evaluation is unchanged and does not yet use native
subagent loading attribution.

## Validating staged skill bundles

`sources/skill_validation.py` judges every skill bundle with a changed file as
one unit before evaluation, because an adoptable path does not make a bundle
valid. A bundle is a folder directly below a `skills/` folder
(`.claude/skills`, `.agents/skills`, an owned root `skills/` or a nested
`<dir>/.claude/skills`). Each change is a `create` (no baseline file), `remove`
(no candidate file) or `update`, and a complete removal is always valid. Any
other candidate membership must keep `SKILL.md` (`missing_entry_point`, so
deleting only the entry point cannot orphan the assets), open it with a
frontmatter fence on line 1 that parses (`missing_frontmatter`,
`bad_frontmatter`; a BOM blocks), give a `description` (`missing_description`),
and name itself after its directory in lowercase letters, digits and hyphens
(`invalid_name`). A name is required outside `.claude/skills`
(`missing_name`), since Codex rejects a skill without one. Inline Markdown links
and used full or collapsed reference links with single-line definitions outside
code must resolve to a file, or a directory holding one, in the bundle
(`dangling_reference`) and stay inside it (`reference_escapes_bundle`); targets
are percent-decoded and normalized, and `<...>` targets may hold spaces. Fenced
code is skipped in one linear pass (a closing fence needs the opener's length and
no info string; an unclosed fence runs to the end). Shortcut reference links,
multiline definitions and the full Markdown grammar are not checked. A newly added file that
`SKILL.md` never mentions by a whole bundle-relative path or unique file name is
an `orphan_asset`. A filename embedded within a longer filename does not count;
when multiple bundle files share a basename, the added file requires its path.
This is a static mention check, not proof that an agent loads or uses the asset.
A newly created bundle whose name equals another bundle's in the candidate state, in
the same kind of folder (`.claude/skills`, `.agents/skills` or a bare `skills`, never across kinds) at
the same, an enclosing or a nested directory, is blocked as `shadowed_name`; sibling
packages do not collide, and updates and removals are not compared.

The check reads staged bytes and never runs them; executable files and
hook-bearing frontmatter still go through the hook contract. `ahl improve`
stops a blocked candidate with status `blocked`, reason
`skill_bundle_validation_failed` and a `skill_review` document of fixed labels
and bundle paths, and `workflow.adopt` refuses it. A removal is journaled like
any adoption, so `ahl promotion --action rollback` restores the whole bundle.
When `ahl.json` `harness_paths` is configured, the snapshot holds only the listed
files, so bundle membership is unknown: the entry point, dangling reference and
orphan asset checks are skipped, the action is reported as `scoped`, and only
the listed `SKILL.md` frontmatter, name and escaping links are judged.
References from other bundles to a removed one, name collisions across kinds of folder, and runtime activation are not checked or
claimed.

## Independently verified format repair

See [repair a supported skill header](repair-skill-header.md) for the commands.

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
journal records the exact deployment and verifies installed bytes. The same guide
describes rollback.

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

## Harness scope receipt

`--scope project|global|project+global` (default `project`) selects the harness layers
of `ahl improve` and `ahl skills invoke`; [the how-to](improve-scope.md) shows the
workflow. This section is the contract.

**Selection.** Only the command line selects a scope. A project `ahl.json` that
carries a `scope` key is rejected as invalid input before any run state exists, so an
untrusted checkout cannot escalate to the user-level layer. `global` and `project+global`
need an explicit `--agent codex|claude`; `auto`, `responses`, a configured runtime, a
remembered runtime and host markers never choose a user-level location. The location is
the runtime's user skill directory: `$CLAUDE_CONFIG_DIR/skills` (else `~/.claude/skills`)
for `claude` and `~/.agents/skills` for `codex`.

**Evaluation order.** Layers run in the runtime's documented load order, user before
project (`precedence.documented_load_order`, `layer_order` and `out_of_scope` come
from the same table the static audit reports: managed and local layers are never
addressed). The documented order concerns instruction files. For skills that share a
name across layers the effective one is not established, so `precedence.same_name_skills`
is `effective_layer_unknown`, `shared_skill_names` lists them, and each file is repaired
only in the layer that holds it.

**Budget and stops.** One run id, one output directory, one controller clock and one
`--timeout` cover every layer. A layer that ends `blocked`, `inconclusive`,
`budget_exhausted` or in a failure stops the run; the layers after it are rows with
`status: not_attempted` and `reason: earlier_layer_<status>`. An error after an earlier
layer committed is reported beside that layer, never instead of it.

**Receipt.** Every scope returns the same keys:

| Key | Meaning |
| --- | --- |
| `scope` | The selected scope. |
| `layers` | One row per selected layer in evaluation order: `layer` (`global` or `project`), `evaluated`, `status`, `changed_files` (relative to the layer's `root`), `recommended_files` (planned but not applied), `reason`, `root` and, when present, `plan`, `plan_sha256`, `deployment`, `journal`, `rollback` and `error`. |
| `layers_evaluated`, `layers_changed` | Layer names that were examined, and that changed at least one file. |
| `changed_files`, `recommended_files` | All layers combined; user-level entries carry a `global:` prefix, project entries stay unprefixed. |
| `blocked` | Layers that are `blocked` or `not_attempted`, each with its `reason`. |
| `status`, `reason` | The worst layer status (a failure, then `budget_exhausted`, `blocked`, `inconclusive`, `applied`, `no_change`) and its reason. Exit codes follow `status` exactly as for the project. |

With one layer the legacy top-level keys (`plan`, `plan_sha256`, `deployment`,
`journal`, `rollback`) stay at the top level as well; `project+global` carries them per
layer and adds `precedence`. The model-driven project path returns the same keys with one
`project` row. Each layer has its own change plan, guarded journal and rollback; a
user-level plan binds the user-level directory as its project and the scope into its
authorization digest, so its identity differs from a project plan's. It carries no
`configuration` reference and none of the project's settings, because the project's
`ahl.json` never governed it.

**Not yet available.** Model-driven evaluation of a user-level layer (it returns
`blocked` with `global_scope_model_evaluation_unavailable`), user-level instruction,
settings and hook layers, choosing the responsible layer for a conflict between layers,
user-level configuration of a default scope, and a deprecation notice on
`ahl skills invoke` are not implemented.

## Change matrix coverage

`tests/integration/workflow/test_change_matrix.py` pins, on synthetic projects, the terminal state of
each surface operation that the journal can currently apply: skill create, update and remove, instruction
rewrite and removal, and Claude hook create and update through a passed `hook_contract_v1`. Each applied row
checks the target bytes, a committed journal receipt and rollback to the original bytes. The same file pins the
rows that must not apply and must leave the target untouched: a no-change candidate, a mixed set holding an
unresolved operation, a blocked skill bundle, a blocked hook contract, a source edit after review and a
candidate modified after review. These rows exercise `adopt()` and the journal directly, with explicit
authorization; they do not make `ahl improve` apply behavioral candidates.

## Target-state application receipt

`adopt_with_receipt()` (which `adopt()` wraps) returns a receipt built by re-reading every installed path
and mode after the journal commits, not by trusting the write. It lists each operation as `create`,
`update` or `remove` with its final hash, mode and a `verified` flag, plus the deployment ID and whether
rollback is still eligible. `status` is `applied_verified` only when every path matches and the deployment is
active; otherwise `applied_unverified`. `activation` stays `unknown` and `behavioral_effect` stays
`not_established`: a file copy proves neither hook execution nor improvement. A replay after a commit is
refused because the project no longer matches the baseline, so nothing is written or journaled twice.
The receipt holds no file content.

## Review and apply a saved plan

`ahl apply RUN [--project DIR] [--authorize-plan SHA256]` reads `change-plan.json`, `baseline/` and
`candidate*/` from one run directory. It loads no model and makes no network call. Without
`--authorize-plan` it only prints a review packet and exits 0 (`awaiting_review`, `no_change`) or 3.

The packet lists each operation (path, action, surface, hashes, modes, added and removed line counts) and
a `binding` of six checks, each `matched` or `changed`: the project path, the configuration digest, the
application policy source, the whole project harness inventory against the plan's `before` membership, the
baseline tree against `before` and the candidate tree against `after`. Any `changed` check makes the status
`stale`. The runtime, analysis, evidence and authorization references are listed in
`binding_not_rechecked`: they identify producer inputs this command cannot recompute. The `evaluation` block
is copied from the run's own `workflow.json` and labelled `saved_report_declared_unverified`; its prerequisite
statuses are shown to the reviewer and never read as authority, because that file can be edited.

Statuses and exit codes:

| Status | Meaning | Exit |
| --- | --- | --- |
| `no_change` | the plan has no operations | 0 |
| `awaiting_review` | bound, permitted and not yet authorized; `authorize_with` repeats the command | 0 |
| `applied_verified` | the journal committed and every installed path and mode was re-read as expected | 0 |
| `applied_unverified` | committed, but a re-read differs; roll back or inspect | 3 |
| `blocked` | bound, but `blockers` names why the policy does not permit it | 3 |
| `stale` | a bound input changed since the plan | 3 |

Invalid input, an unreadable or forged plan, or `--authorize-plan` naming another plan exits 2 before any write;
a guarded transaction that needs recovery exits 1 with its receipt.

The policy permits one thing: a plan whose every operation is recomputed from the staged baseline bytes as the
bounded skill-header repair above (BOM removal before a minimal header, mode preserved). Blockers are
`behavioral_application_requires_live_certification` (a `behavioral_candidate` plan; no authorization,
approval or saved prerequisite status changes this, and the favorable `ahl improve` branch is unchanged),
`repair_not_reproduced_by_independent_check` (a plan labelled as a repair whose bytes are not that repair),
`configuration_requires_recommendation_only` (a project `ahl.json` exists), `unresolved_operation`, and
`project_frozen_read_only`. A permitted, authorized plan is applied by `adopt_with_receipt()` under the project
lock, so its candidate, skill-bundle and agent-definition checks and its journal record apply unchanged; the
record's metadata names `reviewed_plan_apply` and the plan hash. A second run with the same arguments is `stale`
and writes no second journal entry. The target-state receipt is the one described above and asserts neither
activation nor behavior.

An interrupted application is never applied twice. If the write fails before the target is replaced, the project
is unchanged and the same authorized command applies it once. If the process stops after the replace, the target
already holds the reviewed bytes, so the command reports `stale` and writes nothing; finish the recorded
transaction with `ahl promotion PROJECT --deployment ID --action recover` or undo it with `ahl rollback ID
--project PROJECT`. These apply-stage cases are covered by `tests/integration/workflow/test_plan_review.py`.

## Remaining implementation

This is the first deterministic application slice of #77, not completion of its
behavioral controller. `ahl apply` reviews every saved plan but applies only the
independently reproduced skill-header repair; interruption tests cover only its
application stage (planning, evaluation and review interruptions, Codex hook
application and real-runtime evidence remain). Effective instruction hierarchy (#79, whose scoped rewrite
validation and operation-level review are described above; actual loading evidence and
the composed-change interaction evaluation remain), skill lifecycle and
routing utility (#78), native hooks (#80), pinned target-bank integration (#142),
and full executable CLI/skill parity (#143) remain incomplete; the
[receipt parity contract](../skills/harness-self-improvement/references/invocation.md#receipt-parity-with-ahl-improve)
covers the cooperative receipt only. The behavioral gate
has not been relaxed. No favorable smoke result or approval becomes certification.
