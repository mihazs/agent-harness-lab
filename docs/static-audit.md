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
| `instructions` | `instruction` | empty file, launch skip size (Claude), default project-doc size (Codex), uncertain imports |
| `rules` | `path_rule`, `exec_policy` | Claude rule frontmatter settles; Codex exec policy is not checked |
| `hooks` | `hook` | unknown events, fields or handler types, a missing plugin `hooks` wrapper, skipped handlers, invalid `onFailure` |
| `workflows` | `workflow` | the static workflow lint (`meta` first, no forbidden calls) |
| `agents` | `agent` | a definition the runtime skips or rejects, unknown fields, fields above the version pin |
| `commands` | `command`, `skill` | ignored `name` or `paths` keys in commands; skill bundles are not checked yet |
| `plugins` | `plugin` and the plugin part of settings | a manifest without `name`, a marketplace without a `plugins` list |
| `config` | `config` and the configuration part of settings | the allowlisted reader decodes the file; values are not judged |

A settings file the inventory reads for several surfaces, such as
`.claude/settings.json`, counts once in each dimension it holds. Each row has
`status`, the number of surfaces it counted and how many of them were
`conformant`, `nonconformant` and `unknown`, the sorted `reasons` that were not
conformant, and `source`, the first public documentation row at the pin for that
surface.

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
belong to, and in every dimension when it lies inside a `.claude`, `.agents` or
`.codex` folder but has no kind of its own. Refusals that belong to no dimension
are counted in `unattributed_refusals`. Vendored trees are counted in
`vendored_surfaces` and left out of the dimensions.

A blocked inventory (a file, byte, entry, depth or fact bound was exceeded) or an
unsupported runtime version gives the report status `blocked` or `unsupported`,
every dimension `unknown` with `inventory_<reason>`, and no findings, never a
partial pass.

## Findings

Each surface that is not conformant gives one finding with an `id`, its
`dimension`, `status`, `reason`, checkout-relative `path` and `path_sha256`,
inventory `kind`, `evidence_kind` (`count`, `lexical` or `structural`) and
`source`. A nonconformant finding also has a fixed `remediation` label and a
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
