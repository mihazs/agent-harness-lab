# Static harness audit

`agent_harness_lab.audit` reports, without a model, whether the harness surfaces
of one checkout follow the conventions the pinned runtime documents. It is the
cold-start companion of `ahl analyze`: it needs no captured traces, no credentials
and no runtime. Run `ahl audit PROJECT --runtime codex --json` for a JSON report,
or omit `--json` for a human-readable summary. `--runtime claude-code` selects
the other pinned profile; the default is `codex`. A private JSON copy is stored
under `$XDG_STATE_HOME/agent-harness-lab/audits` (or the usual local state
directory); the command refuses state storage inside the project. The report
path is printed to stderr. Exit 0 means completed, 3 means blocked or unsupported,
and 2 means invalid input or a storage error. `next_action` suggests a behavioral
evaluation when applicable; it is not an instruction to apply a change.
The repository's composite Action reports these findings as pull-request annotations;
see [annotate pull requests](audit-pull-requests.md).
The CLI's `coverage` rows summarize six audit areas. Instructions, skills, and
enforcement use the corresponding inventory evidence; sensors, memory and state,
and the improvement loop remain `not_checked`. An absent skill is `unknown`,
not a pass. CLI findings use `weak`, `missing`, `unknown`, or `not_checked`.
When the modeled default Codex chain drops instruction bytes, coverage is `missing`;
an unsettled dropped-byte count does not get that label. Coverage can
also be `conforms`. The library's separate report retains its documented
`conformant`, `nonconformant`, and `unknown` vocabulary.
The separate `agent_harness_lab.audit.protected_paths.capture()` inventory lists
common secret, configuration and CI path candidates using checkout metadata only;
it never opens their contents. This private, bounded inventory does not determine
whether any path is protected. Refused paths leave coverage unknown. The walk is
not an atomic filesystem snapshot. It shares the inventory walk, so in a checkout
with a `.git` entry at its root it does not enter a directory the checkout's own
`.gitignore` files exclude (see [ignored
directories](#ignored-directories-and-the-entry-bound)): a secret, configuration or
CI path below one is not listed, while an ignored file in a walked directory is.
`coverage: walked_checkout_metadata_only` does not say those directories were walked,
and the report counts none of them, so a path absent below an ignored directory is
unknown, not clear.
The CLI includes this separately captured inventory under `protected_path_inventory`;
it is not covered by `audit_sha256`, which binds the harness inventory audit.

The separate private `agent_harness_lab.audit.permission_candidates.report()` API
compares these paths against hashed exact `Edit(path)` and `Write(path)` deny
entries in declared Claude project settings. A match is a lexical candidate only;
runtime enforcement stays unknown. Missing matches, unsupported glob syntax,
private local settings and other runtime layers remain unknown. The two inputs
are independently captured snapshots. Rule hashes and paths are private metadata.
The CLI includes this comparison under `permission_candidates`; it is outside
`audit_sha256` and does not change the enforcement coverage verdict.
The audit CLI and audit convenience capture use harness inventory schema version 2
for these facts. Direct `harness_inventory.capture()` calls default to version 1
for compatibility; pass `schema_version=2` to include the newer evidence in the
versioned inventory and its hash. A version 1 inventory leaves these candidates unknown.
The CLI also reports `protected_path_hook_review`, a checkout write-tool hook
candidate and a prompt to review applicable permissions and hooks for each path.
The hook matcher concerns tool names alone; it does not establish that a handler
inspects or protects any path. Absent, unsupported and unavailable candidates
remain unknown. This observation is also outside `audit_sha256`.

The separate private `agent_harness_lab.audit.constraint_references.report()` API
accepts caller-supplied instruction bytes only when their digest matches a parsed
checkout inventory node. It emits bounded line-number and hash references for
lexical hard-constraint cues and exact protected-path references, without line
text. For a direct lexical write constraint (including “never weaken”)
mentioning an inventoried protected
path, a well-formed supported command handler in Claude project settings with a
recognized `PreToolUse` Edit/Write or all-tools matcher is a `hook_candidate_present`.
Disabled, malformed, conditional and other handler forms stay unknown. The matcher concerns tool names,
not paths: the handler's behavior, loading, execution and enforcement stay unknown.
For Codex, only a project `.codex/hooks.json` command handler with an exact
`apply_patch` or `^apply_patch$` matcher is considered a candidate. The pinned
[Codex source](https://github.com/openai/codex/blob/d786ff91fad8e7ddab8653ee07bc6313a6fcb03d/codex-rs/core/src/tools/handlers/apply_patch.rs)
declares an `apply_patch` pre-tool event; trust, loading and handler behavior are
not established by this inventory. Unsupported or malformed top-level fields,
other matchers, hook forms and configuration
layers remain unknown; no match is not evidence of missing enforcement. Lexical
cues are not proof of a rule or its enforcement.
Reference hashes and paths are private and may
be guessable. Inputs are separate observations, not an atomic snapshot.
The CLI re-reads eligible checkout instruction files through bounded, no-follow
access and checks their digests against the inventory. The resulting private
`constraint_references` observation is outside `audit_sha256`; unavailable files
leave it incomplete, and its coverage and enforcement stay unknown.

The `hook_events` field lists known event names from declared hook handlers. Its
coverage is `unknown` when hook surfaces are unavailable or could not be parsed;
otherwise `declared_only` describes inventory coverage, not execution or enforcement.
The private harness inventory also fingerprints observed keys in Claude skill
frontmatter hook mappings. The reader does not validate nested handler structure;
the hook declaration remains unknown, and handler content is not retained. Unsupported
frontmatter and other hook forms remain unknown. These fingerprints do not establish
that a skill was loaded or a hook ran, and are not included in `hook_events`.
Embedded skill or agent hooks that have not been fully inspected leave event
coverage unknown, even when other declared events are reported.
Enabled or inherited plugins whose content was not selected, and plugin manifests
whose hook components are not fully resolved, also leave event coverage unknown.
For explicitly selected plugin checkouts, `plugins_inventory.capture(...,
schema_version=2)` records whether a plugin manifest has a `hooks` key. Its value
is unvalidated and not retained. An absent key says nothing about default plugin
hooks, loading or enforcement; the audit CLI does not select plugin checkouts.
Memory contents are not read. Other prose-to-hook matching is not checked.
Try the [offline walkthrough](audit-walkthrough.md) with a deliberately flawed
synthetic fixture before auditing a real checkout.

```python
from agent_harness_lab import audit

report = audit.run('/path/to/checkout', 'claude-code')   # or 'codex'
document = report.document()                              # private JSON-ready mapping
```

`run()` captures the [read-only harness surface inventory](source-contracts.md#read-only-harness-surface-inventory)
at the runtime's `surfaces.PINS` version and passes it to `audit(inventory)`. The
audit reads only the facts the inventory parsers already recorded. It does not
reread, execute, import or load a file, write the checkout, open a network
connection or call a model. The document fixes `model_calls`, `executed_files` and
`files_written` at `0` and `network_used` at `false`.

## Dimensions

The report has one row per dimension, always in this order:

| Dimension | Inventory kinds | Checked facts |
| --- | --- | --- |
| `instructions` | `instruction` | empty file, launch skip size (Claude), default project-doc size and the [Codex project-doc budget](#codex-project-doc-budget) over the directory chain, uncertain imports; a Claude `AGENTS.md` stays `unknown` (`instruction_files_setting_unknown`); a plugin-root `CLAUDE.md`, which the runtime never loads, stays `unknown` (`instruction_not_loaded_by_runtime`) |
| `rules` | `path_rule`, `exec_policy` | Claude rule frontmatter settles; Codex exec policy is not checked |
| `hooks` | `hook` | unknown events, fields or handler types, a missing plugin `hooks` wrapper, skipped handlers, invalid `onFailure`; a file with no handlers is `unknown` (`no_hooks_declared`) |
| `workflows` | `workflow` | the static workflow lint (`meta` first, no forbidden calls) |
| `agents` | `agent` | in a project `.claude/agents` or `.codex/agents` folder: a definition the runtime skips or rejects, unknown fields, fields above the version pin; elsewhere `unknown` (`agent_layer_not_modelled`) |
| `commands` | `command`, `skill` | ignored `name` or `paths` keys in commands; each `SKILL.md` against the [skill metadata and routing rules](#skill-metadata-and-routing-collisions) |
| `plugins` | `plugin` and the plugin part of settings | settings that declare no plugin or marketplace are `unknown` (`no_plugins_declared`); a manifest without `name` and a marketplace without a `plugins` list are checked only in a plugin checkout inventory passed to `audit()` |
| `config` | `config` and the configuration part of settings | the allowlisted reader decodes the file; values are not judged |

A settings file the inventory reads for several surfaces, such as
`.claude/settings.json`, counts once in each dimension it holds. Each row has
`status`, the number of surfaces it counted and how many of them were
`conformant`, `nonconformant` and `unknown`, the sorted `reasons` that were not
conformant, and `source`, the first public documentation row at the pin for that
surface.

`run()` never inventories plugin manifests or marketplaces in the checkout. To
check them, pass the inventory of a selected plugin checkout to `audit()`:

```python
from agent_harness_lab.sources import plugins_inventory

plugins = plugins_inventory.capture(checkout, profile, plugin_checkouts={'name@market': '/path/to/plugin'})
report = audit.audit(plugins.checkouts[0].inventory)
```

That report covers every dimension of the plugin checkout. A plugin-root
`CLAUDE.md` is `unknown` (`instruction_not_loaded_by_runtime`) there, because
the runtime does not load it, so no size or emptiness rule applies.

## Codex project-doc budget

Codex reads one file per directory from the project root to the task directory
(`AGENTS.override.md`, else `AGENTS.md`) under a single `project_doc_max_bytes`
budget, 32 KiB by default (`DEFAULT_PROJECT_DOC_MAX_BYTES` in
[`codex-rs/config/src/config_toml.rs`](https://github.com/openai/codex/tree/d786ff91/codex-rs/config/src/config_toml.rs)).
Following `read_agents_md` in
[`codex-rs/core/src/agents_md.rs`](https://github.com/openai/codex/tree/d786ff91/codex-rs/core/src/agents_md.rs)
at the pinned `openai/codex@d786ff91`, a file larger than the remaining budget
is truncated to it, a file that is blank to Rust's `str::trim` uses no budget,
and once the budget is spent every later file is skipped. Separators and the
`CODEX_HOME` file use none of it.

`run()` follows that chain for `task_cwd` and replaces the per-file verdict of
each file the default budget cuts:

- `instruction_chain_truncated_at_default_project_doc_bytes`: the file that
  crosses the budget.
- `instruction_chain_skipped_at_default_project_doc_bytes`: a non-blank file
  after the budget is spent.

Both are `nonconformant` with `evidence_kind: structural`, the remediation
`shorten_the_instruction_chain_or_raise_project_doc_max_bytes` and `measures`
`budget_bytes`, `loaded_bytes` and `dropped_bytes`, so the finding names the file
and how much of it Codex drops. A chain that fits, even exactly, adds nothing.
Only checkout files are matched against the chain: a same-named file in a
caller-declared external root, such as `CODEX_HOME`, keeps its per-file verdict.
When the cut ends exactly where a file's leading whitespace ends, the loaded
prefix is blank, so the file is reported truncated with `loaded_bytes` 0 and all
of its bytes dropped, and the budget carries on unchanged to later files.

The chain is modelled only at the documented defaults: a 32 KiB budget, the
`.git` project root marker and no fallback file names. A budget, marker or
fallback list set in configuration is never read, and an unknown project trust is
treated as trusted. The audit reports no budget outcome when it cannot settle
one: when the checkout root has no `.git` (Codex would look above the checkout),
when the project is `declared_untrusted` (Codex loads no project docs), or when
`audit()` gets an inventory whose `task_cwd` it cannot recover from the listed
paths. The walk stops at the first directory whose candidate was refused, such as
a symlink Codex would follow, or was not parsed, and after a cut that ends inside
a file's leading whitespace, where a split multi-byte character would change the
outcome. That file is still reported truncated, but with `budget_bytes` alone,
since its loaded and dropped bytes are not settled; files before it keep their
budget findings. The budget check
never makes a file or dimension conformant.

## Skill metadata and routing collisions

Each `SKILL.md` in an adoptable skill root is one `skill` surface; the other files of its bundle
are part of that skill and add no surface of their own. The header goes through the shared
bounded frontmatter reader (`sources.frontmatter`), never a YAML load, and `SKILL.md` is the only file read:
no script of the bundle is imported or executed. A block the reader cannot model is `unknown`
(`skill_frontmatter_unsupported`). One reason is reported per skill, in this order, and each cites a public page:

| Reason | Rule | Source |
| --- | --- | --- |
| `skill_name_invalid` | a declared `name` is not lowercase letters, digits and single hyphens, or is over 64 characters | [Agent Skills](https://agentskills.io/specification) |
| `skill_name_reserved_word` | a Claude Code name contains `anthropic` or `claude` | [skill best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices) |
| `skill_name_differs_from_directory` | a declared `name` differs from the bundle directory | Agent Skills |
| `skill_description_missing`, `skill_description_not_text` | no description, or a bare YAML boolean or null, or a list or mapping | Agent Skills |
| `skill_description_too_long` | over 1024 characters | Agent Skills |
| `skill_reference_missing` | a relative Markdown link outside code fences names a file or folder the inventory did not list in the bundle; URLs, absolute, anchor-only and `..` links are not judged | Agent Skills (file references) |
| `skill_description_lacks_trigger_phrasing` | no when/trigger wording (`when`, `whenever`, `trigger`, `invoke`, `activate`, `if the user`, `use this for`, `use to`) | the [Claude](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices) or [Codex](https://developers.openai.com/codex/skills) page for the audited runtime |
| `skill_body_without_references` | more than 500 body lines and no `references/` folder | Agent Skills |
| `skill_routing_collision` | see below | the runtime page above |

A missing `name` is not reported, because both pinned runtimes default it to the directory. The trigger
rule is a lexical heuristic with known false positives and negatives: `when` in a what-only sentence passes, and a trigger
phrased without these words is reported, so it carries `evidence_kind: lexical`. Remediation labels
name the cheapest edit; a header or link fix is `deterministic_edit` or `content_edit`.

A skill with no violation is `conformant` only when the audited runtime's own root holds it: `.claude/skills/<name>` for
Claude Code (also nested `<dir>/.claude/skills`, which load on demand) and `.agents/skills/<name>` for Codex on
the `task_cwd` chain. A skill in another runtime's root, a project `skills/` tree no runtime discovers, a Codex
folder off the chain, or a Codex skill whose chain is unsettled is `unknown`
(`skill_not_loaded_by_runtime`, `skill_load_scope_unsettled`), never a pass; a violation in it is still reported.

**Routing collisions.** The description is tokenized by `ahl-skill-tokens-v1`: lowercase alphanumeric
words of three or more characters, minus a fixed stop-word list, no stemming, at most 200. Two skills collide
when the Jaccard similarity of the token sets is at least the threshold, `0.5` by default
(`collision_threshold` on `audit.run()` and `audit.audit()`, in `(0, 1]`), under the rule
`ahl-skill-collision-jaccard-v1`; each side needs at least four tokens. The report records the rule, tokenizer and
threshold (`skill_collision_rule`, `skill_tokenizer`, `skill_collision_threshold_permille`), so a result can be
reproduced. Only skills that load together are compared: both in the audited runtime's root, in the same folder or one
nested below a folder that encloses the other. Sibling directories such as `a/.claude/skills` and `b/.claude/skills`
are never paired, and neither are skills whose description was not read or not text. Each skill in a colliding pair
gets a `skill_routing_collision` finding (`behavioral_evaluation_required`) whose `related` field lists at most
five of the other skill paths (the first in sorted order; `related_total` in `measures` is the full count) and whose
other `measures` are the strongest pair's `shared_tokens`, `union_tokens`, `similarity_permille` and
`threshold_permille`. A skill that already has another finding reports that reason first; the collision appears
once it is fixed. The inventory stores only hashes of the description tokens and of the relative link targets, never the words or paths. A collision is
a lexical overlap, not evidence that a runtime mis-routes: confirm it with a routing evaluation.

## Statuses

- `nonconformant`: at least one surface has a fact the pinned public contract
  names as ignored, skipped, rejected or unknown by the runtime.
- `conformant`: the dimension has surfaces and every one of them conforms.
- `unknown`: anything else. A dimension with no surface is `unknown` with
  `no_surface_declared`, since absence does not show that a convention is
  followed.

A surface is `unknown`, never `conformant`, when it was not read
(`private_excluded`, such as `CLAUDE.local.md` or `.claude/settings.local.json`),
not parsed (`not_parsed`, such as hook scripts), failed to parse
(`parse_failed`), is unsupported for the runtime (`unsupported`), has no parser
(`no_parser`), has no check yet (`not_checked`) or no capability row at the pin
(`no_pinned_capability`). An entry the inventory refused, such as a symlink or
hardlink, is `unknown` with `refused_<reason>` in each dimension its path would
belong to, and in every dimension when it lies inside a harness folder of the
audited runtime (`.claude` for Claude Code, `.codex` or `.agents` for Codex) but
has no kind of its own. An entry in the other runtime's folder is not a surface
of the audited one. Refusals that belong to no dimension
are counted in `unattributed_refusals`. Vendored trees are counted in
`vendored_surfaces` and left out of the dimensions.

A blocked inventory (a file, byte, entry, depth or fact bound was exceeded) or an
unsupported runtime version gives the report status `blocked` or `unsupported`,
every dimension `unknown` with `inventory_<reason>`, and no findings, never a
partial pass.

### Ignored directories and the entry bound

The inventory examines at most 20,000 directory entries. In a checkout that has a
`.git` entry at its root, it neither counts nor enters a directory that the
checkout's own `.gitignore` files exclude, such as an installed dependency or build
folder, so a long-lived checkout is inventoried like a fresh one. The walk reads
only `.gitignore` files below the checkout, as ordinary bytes, in a conservative
subset of git's rules: `*`, `?`, bracket classes, `**`, a leading or middle `/`
anchor, a trailing `/` and `!` negation, with the last matching line deciding and a
deeper file overriding a shallower one. A line it cannot model exactly (a
backslash escape, a POSIX class or `[^` set, an unbalanced bracket, an empty
segment, too many segments or `**` wildcards), an unreadable, non-UTF-8 or
non-regular `.gitignore` or more rules than its bound make that file, and every
directory below it, count as not ignored: the walk then reads everything, as
before, and never skips on a guess. The user's global excludes file, `.git/info/exclude` and the
index are never consulted, so a file that is tracked inside an ignored directory
is not inventoried. The root `.claude`, `.agents` and `.codex` folders and the root
`skills` tree, each with everything below it, and the `--task-cwd` directory and its
parents are walked even when ignored.
Without a `.git` entry nothing is skipped. Refusals under an ignored directory,
such as a symlink or nested repository, are not reported. The protected-path and
feedback-sensor inventories use this walk and skip the same directories; their
`coverage` value does not record the omission, so read their lists as incomplete
below an ignored directory.

When the entry bound is still exceeded, the report keeps `blocked_reason:
entry_bound_exceeded` and adds `inventory_remedy`: `max_entries` and the largest
top-level directories as `{directory, entries, at_least}` rows, names and counts
only (`at_least` is true where counting stopped at the bound). `next_action` names the
two ways out: add a listed directory that holds no harness files to `.gitignore`, or
rerun with `--max-inventory-entries N` (1 to 200,000, for that run only). The
human output prints the same ranking. Raising the bound reads more of the tree, so
it is not a substitute for ignoring a dependency folder. See
[audit a long-lived checkout](audit-long-lived-checkout.md) for the steps.

## Findings

Each surface that is not conformant gives one finding with an `id`, its
`dimension`, `status`, `reason`, checkout-relative `path` and `path_sha256`,
inventory `kind`, `evidence_kind` (`count`, `lexical` or `structural`),
`source` and `measures`, sorted `(label, non-negative integer)` pairs that are
empty unless a check counts something, such as the
[Codex project-doc budget](#codex-project-doc-budget). A nonconformant finding also has a fixed `remediation` label and a
`cost_class`: `deterministic_edit`, `content_edit` or
`behavioral_evaluation_required`. Nonconformant findings come first, cheapest
`cost_class` first, followed by unknown findings.

## Instruction precedence

The private report includes `instruction_precedence`, a deterministic list of
declared instruction files and documented load order. Codex's same-directory
selection and closer-directory relations are conditional on default root and
fallback settings and the files being loaded within the budget. A relation names
the higher and lower file, its rule and its source. The inventory cannot verify
runtime settings, trust, external user or managed files, imports, or actual loading.
Refused files and those unchecked layers remain `not_checked`.
The `--task-cwd` option selects the project-relative task directory's chain;
the default is the project root. The task directory must exist within the project
and cannot cross a symlink.

For Codex, [the documented chain](https://developers.openai.com/codex/guides/agents-md)
checks `AGENTS.override.md` before `AGENTS.md` in each directory and gives later,
closer files precedence when both load. For Claude Code, [the documented load
order](https://code.claude.com/docs/en/memory) is managed, user, project, local;
that order does not guarantee a winner for conflicting instructions. Neither
report compares instruction content or proves that an agent obeyed a file.

## What an audit finding does and does not establish

Every dimension and finding carries
`notice: convention_check_not_performance_evidence`. A conformant row means the
declared files match the documented format at the pin, not that a runtime loaded
them or that the harness performs well. A nonconformant row means a documented
convention is not followed, not that the harness performs worse. The report has
`aggregate_score: null`, `performance_evidence: false` and
`publication_allowed: false`: it holds checkout paths and stays private. A
behavioral claim still needs an evaluation such as `ahl improve`.

## Claude declared instruction totals

The CLI's private JSON report includes `declared_instruction_totals` for Claude
Code. It counts distinct resolver-admitted checkout launch files and their
resolved imports, including imports with other filenames. The values describe a
bounded checkout snapshot under the assumed default instruction-file setting;
they exclude user and managed instructions and are not evidence of what a
runtime loaded. Missing or uncertain imports, cycles, excluded private launch
files, changed matching inventory content, or an unavailable resolver leave the
totals `unknown`. Contents are not emitted and publication is not allowed. The resolver is a
separate filesystem observation from the harness inventory. Only recognized
instruction files are compared across observations; arbitrary imports are not
in the first inventory. Cross-observation consistency is therefore unknown.

The CLI's `--instruction-soft-lines` sets a checkout launch-line convention
(default 200). If a settled declared total exceeds it, the report emits a `weak`
count hypothesis to evaluate, not an instruction to edit. The default extends
Claude's [per-file rule of thumb](https://code.claude.com/docs/en/features-overview)
to the captured launch closure; it is not a validated aggregate threshold.
Unknown totals never produce this hypothesis.

For a settled, nonempty Claude or Codex checkout closure, the CLI also emits lexical hypotheses when it
finds no constraint wording or common verification command, and a structural
hypothesis for a section with at least five numbered steps or three shell-labelled fenced
blocks. These are deliberately simple patterns: they can miss equivalent
wording and match examples. Findings need behavioral evaluation and do not quote
instruction contents or headings. Unlabelled and other-language fences do not
count toward the shell-block cue; older findings based solely on them will not
pass a fresh procedure recheck. No shape hypothesis is emitted for an
unknown closure. An empty resolved Claude import also produces a weak
hypothesis; missing imports and cycles leave totals unknown with a reason.


## Codex declared instruction totals

The private CLI report counts the nonblank selected checkout project chain when
the default root and filename assumptions are settled and the chain is not
truncated. It does not include CODEX_HOME or instructions above the checkout,
and does not establish runtime loading. The resolver reads a separate checked
filesystem observation; it compares selected file hashes to the inventory, but
cross-observation consistency remains unknown. Truncated, refused, oversized,
changed, or otherwise unsettled chains produce unknown totals.
This includes a budget cut within multibyte whitespace, whose lossy-decoded
prefix can consume the remaining budget, and a later file exceeding the budget
after an ignored blank prefix.

Pass `--codex-project-doc-max-bytes N` to model an operator-declared nonnegative
byte budget; the CLI does not read or authenticate the runtime's settings. If
omitted, it models the documented 32 KiB default. A declared limit replaces the
default for chain findings, which identify dropped bytes and the crossing file
when those are settled. The default 200-line soft budget is a configurable
convention extrapolated to the selected closure, not a validated threshold.

`--codex-budget-boundary` adds a separate private observation when a byte
budget cuts inside leading whitespace. It models the pinned runtime's lossy
UTF-8 decoding from bounded re-read bytes that match the selected inventory, and
may name additional truncated or skipped files. Missing, changed or otherwise
unsettled inputs leave the observation unknown. This does not modify the core
audit findings or their identity: the separate observations are not atomic,
and runtime settings and actual loading remain unknown. Without the option the
existing report shape is unchanged.
The direct `budget_boundary.report()` API accepts caller-supplied totals and
boundary maps; it does not authenticate them or prove that a resolver produced
them. Its result grants no authority.

For supported instruction findings, `improvement_hint` provides a fixed,
content-free `focus` string and the audited runtime. Before using it with
`ahl improve --focus`, select an improvement runtime compatible with the audited
runtime and the configured profile. The audit does not select or override an
improvement runtime. The hint carries no paths, counts, source text or finding identity and does
not establish a defect or authorize an edit. The report is not automatically
read by improve: focus is an optional user-selected hint, subject to the normal
scope, evaluation and promotion checks. Closed-input profiles that prohibit
focus reject it; the hint does not override those profiles. An improvement run
may invoke a model or agent and supply it with authorized project data, unlike
the offline audit. The content-free hint does not change that exposure.

### Rechecking a convention for improvement

For an explicitly requested improvement, `ahl audit PROJECT --improvement-recheck`
includes the declared runtime, task directory and Codex byte budget in its private
report. Pass its `report.json` to `ahl improve PROJECT --from-audit REPORT` to
re-audit the checkout and use an eligible finding as a fixed, content-free
proposer focus. Codex truncation, skipped-file, instruction procedure-shape and
skill-description collision findings are eligible; other conventions are not.
The procedure shape and routing collisions are lexical hypotheses, not evidence
that a skill is needed or that routing failed. The procedure check re-observes
selected instruction files separately and rejects the hypothesis if that observation
is unsettled or the chain is truncated. It cannot be combined
with `--focus` or deterministic contract repair. When the fresh inventory exceeds
the entry bound, `ahl improve` stops before staging anything or calling a model:
the receipt has `status: blocked`, `reason: inventory_entry_bound_exceeded`, the
same `inventory_remedy` ranking and no changed files. Ignore the large directory
and run `ahl audit` again to obtain a report for the recheck.

The supplied report is an untrusted selection and its parameters are caller
assertions. The recheck requires a matching inventory and freshly observed
finding, and repeats the observation around the baseline snapshot. These are
separate observations, not an atomic snapshot or proof of runtime loading.
The private result and any resulting change plan label the hypothesis as a static
convention with no authority. Normal evaluation and adoption gates remain in
force; a recheck does not guarantee a candidate or a completed evaluation. The
plan metadata is descriptive, outside the common plan's integrity digest. Evaluation
feedback is not written back to the audit report.

To include the caller's soft line budget in a recheck, use
`ahl audit PROJECT --improvement-recheck-soft-budget --instruction-soft-lines N`.
This emits `ahl-audit-recheck-parameters-v2`; a matching, untruncated instruction
chain above that budget is eligible as a simplification focus. The threshold is a
caller-declared convention, not a validated performance limit. The
soft-budget hypothesis metadata labels its threshold `caller_declared`; the
convention URL is a general reference, not the source of that threshold. Existing
`--improvement-recheck` output remains v1 and does not support a soft-budget
recheck; older consumers do not accept the v2 parameters.

### Staging static conventions on a first run

`ahl improve --history` runs this audit itself, without an audit report, when the history
has findings and no verified evaluation outcome, and no `--focus` or `--from-audit` is
given. The audit is fresh, read-only, bounded and uses no model. Eligible conventions for the
selected `codex` or `claude` runtime are, most content-losing first: a Codex instruction chain
the project-document budget truncates, an oversized instruction file
(`instruction_exceeds_launch_bytes` for Claude, `instruction_exceeds_project_doc_bytes` for Codex), a
skill-description routing collision, an instruction chain above the default 200-line convention (an
assumed default, labelled `threshold_origin: assumed_default`, not a validated limit) and a
procedure-shaped instruction section. The audit covers the project root with the default budget, so the
skipped-chain and declared-budget reasons that `--from-audit` accepts cannot arise here. The
truncation, routing, line and procedure conventions reuse the focus texts of `--from-audit`; the two
oversized-file conventions use their own focus text, and `--from-audit` does not accept their reasons.
The chain, line and procedure conventions depend on settled instruction totals.

One convention is staged per run so the paired evaluation tests one change; the others are listed
as `not_selected` in `static_convention_candidates`. The hypothesis is labelled
`hypothesis_source: static_convention` with `selection: automatic_no_trace_candidate`, kept
distinct from trace evidence. It never lowers the evidence bar and is never applied by default.
When nothing is eligible the run ends with the first-use `no_outcome_evidence` block and no model
call, and `findings_not_candidates.static_audit` carries the audit's cause. It lists `checked` (the conventions the audit
evaluated) and `unsettled` (applicable conventions it could not evaluate), and `why` is one of
`no_eligible_convention` (every applicable convention was checked and none applies),
`instruction_totals_unsettled` (the instruction chain could not be settled, for example Codex without
a Git project root, an unresolved Claude import or an unreadable chain, so the truncation, line and
procedure conventions are unknown, not clean; `instruction_totals_reasons` names why and the cause
carries a remediation) or `audit_inventory_<reason>` (the inventory is blocked and nothing was
checked). Evaluation feedback is not written back to the audit report.

### Feedback sensor candidates

`ahl audit PROJECT --feedback-sensors` adds a private filename-only inventory for
recognized test paths, configuration files, task runners and Git hook configurations.
A candidate's `stage` is a filename convention, not proof that a check exists,
runs early or executes successfully. Contents and commands are not read by this
inventory. `coverage` describes only the metadata walk: refused paths make it
unknown, and unrecognized filenames mean detection completeness is unknown even
when the walk completed. The `.github/workflows`, `.circleci`, and `.buildkite`
subtrees are excluded; other hosted configuration paths may not be recognized.
The walk is the inventory walk, so in a checkout with a `.git` entry at its root a
directory the checkout's own `.gitignore` files exclude is not entered (see
[ignored directories](#ignored-directories-and-the-entry-bound)): a test path,
configuration file or task runner below one, and a `tests` directory name there, is
not listed, while an ignored file in a walked directory is. `walked_checkout_metadata_only`
does not say those directories were walked, and the report counts none of them, so
a candidate absent below an ignored directory is unknown, not missing.
Instruction-to-command consistency and stale-target
detection are not checked in this slice. The inventory is a separate metadata
observation, not an atomic snapshot with other audit inputs. Without the option,
the existing report shape is unchanged.

### Root npm test observation

`ahl audit PROJECT --npm-test-observation` adds a private, bounded comparison of
literal `npm test` or `npm run test` mentions in the checkout-root instruction
file with the presence of `scripts.test` in the root `package.json`. It does not
return instruction or script text, execute npm, or establish whether a command is
valid, stale, applicable or effective. A mention can occur in a negated sentence.
A declared script can be empty, and a missing declaration concerns only that root
file, not workspaces, inherited settings or other runners. Loading and execution
remain unknown. The instruction file must match its inventory digest; the files
are separately observed rather than an atomic snapshot. Missing, changed,
malformed, oversized or linked files and nested task directories leave the
comparison unknown. Without the option the existing audit report shape is
unchanged.
