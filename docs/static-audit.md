# Static harness audit

`agent_harness_lab.audit` reports, without a model, whether the harness surfaces
of one checkout follow the conventions the pinned runtime documents. It is the
cold-start companion of `ahl analyze`: it needs no captured traces, no credentials
and no runtime. A command-line entry point is planned under
[issue #328](https://github.com/mihazs/agent-harness-lab/issues/328); this page
documents the library contract it will print.

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
| `commands` | `command`, `skill` | ignored `name` or `paths` keys in commands; skill bundles are not checked yet |
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
outcome; files before that point keep their budget findings. The budget check
never makes a file or dimension conformant.

## Statuses

- `nonconformant`: at least one surface has a fact the pinned public contract
  names as ignored, skipped, rejected or unknown by the runtime.
- `conformant`: the dimension has surfaces and every one of them conforms.
- `unknown`: anything else. A dimension with no surface is `unknown` with
  `no_surface_declared`, since absence does not show that a convention is
  followed.

A surface is `unknown`, never `conformant`, when it was not read
(`private_excluded`, such as `CLAUDE.local.md` or `.claude/settings.local.json`),
not parsed (`not_parsed`, such as skill files and hook scripts), failed to parse
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

## What an audit finding does and does not establish

Every dimension and finding carries
`notice: convention_check_not_performance_evidence`. A conformant row means the
declared files match the documented format at the pin, not that a runtime loaded
them or that the harness performs well. A nonconformant row means a documented
convention is not followed, not that the harness performs worse. The report has
`aggregate_score: null`, `performance_evidence: false` and
`publication_allowed: false`: it holds checkout paths and stays private. A
behavioral claim still needs an evaluation such as `ahl improve`.
