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
whether any path is protected. Refused paths leave coverage unknown; rule and hook
matching is not yet implemented. The walk is not an atomic filesystem snapshot.
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
