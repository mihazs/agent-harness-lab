# Harness surfaces

Use this reference before changing any harness file other than a root instruction
file or a skill bundle. `agent_harness_lab/surfaces.py` owns every answer below;
when this page and that module disagree, the module wins and this page is stale.

## Rules

- Inventory first, read-only. `ahl sources inventory CHECKOUT --runtime RUNTIME
  --version VERSION` lists surface files without executing, importing or writing
  anything, and reads no root outside the checkout unless one is declared with
  `--external-root`.
- Executable surfaces (hook scripts, workflow scripts, files with an execute bit or
  an executable suffix) are never executed during analysis.
- Another repository is never edited in place. A selected plugin checkout gets
  recommendations or a private patch bundle; files in a nested repository keep
  their recommendation-only or unsupported verdict.
- A listed file is a declaration, not evidence that a runtime loaded it. Loading
  and effectiveness stay `unknown` unless a pinned native capture shows them.
- Use synthetic fixtures for every example and test. Never copy a project's
  surface contents, paths or findings into shared guidance.

## Surface matrix

Each row gives the writability and the route of one surface, for one synthetic
checkout-relative path per runtime, inside the checkout. `surfaces.matrix()`
computes it from the classifier rows, and the inventory CLI prints the declared
runtime's column.

| Surface | Claude Code | Codex |
| --- | --- | --- |
| `instructions` | `adoptable` / adopt | `adoptable` / adopt |
| `path_rules` | `adoptable` / adopt | `recommendation_only` / recommendation |
| `hooks` | `contract_required` / contract | `contract_required` / contract |
| `workflows` | `contract_required` / contract | no file surface |
| `agents` | `adoptable` / contract | `adoptable` / contract |
| `commands_skills` | `adoptable` / adopt | `adoptable` / adopt |
| `plugins` | `recommendation_only` / recommendation | `recommendation_only` / recommendation |
| `mcp` | `recommendation_only` / recommendation | `recommendation_only` / recommendation |
| `output_styles` | `recommendation_only` / recommendation | `recommendation_only` / recommendation |
| `memory` | `private_excluded` / excluded | `private_excluded` / excluded |
| `settings` | `recommendation_only` / recommendation | `recommendation_only` / recommendation |

Representative paths (synthetic):

| Surface | Claude Code | Codex |
| --- | --- | --- |
| `instructions` | `CLAUDE.md` | `AGENTS.md` |
| `path_rules` | `.claude/rules/*.md` | `.codex/rules/*.rules` (exec policy) |
| `hooks` | `hooks` key of `.claude/settings.json` | `.codex/hooks.json` |
| `workflows` | `.claude/workflows/*.js` | none |
| `agents` | `.claude/agents/**/*.md` | `.codex/agents/**/*.toml` |
| `commands_skills` | `.claude/skills/<name>/` | `.agents/skills/<name>/` |
| `plugins` | plugin enablement in `.claude/settings.json` | `[plugins]` in `.codex/config.toml` |
| `mcp` | `.mcp.json` | MCP keys in `.codex/config.toml` |
| `output_styles` | `.claude/output-styles/*.md` | instruction keys in `.codex/config.toml` |
| `memory` | `.claude/agent-memory/` | `memories/` below a declared `CODEX_HOME` |
| `settings` | other keys of `.claude/settings.json` | other keys of `.codex/config.toml` |

## Writability and routes

When several classifier rows match a path, the strongest writability wins, in the
order `private_excluded`, `adoptable`, `contract_required`, `recommendation_only`,
`unsupported`. The single-run controller routes each writability:

- **adopt** (`adoptable`): a candidate may change the file in its staged copy.
  Application still follows the evaluation and application policy; nothing is
  written to the project because a path is adoptable.
- **contract** (`contract_required`, or an adoptable path whose edits carry the
  hook facet): a change plan keeps the operation unresolved until a passed
  `hook_contract_v1` verdict names it. Agent definitions are adoptable files, but
  their frontmatter can register hooks, so every agent edit takes this route.
- **recommendation** (`recommendation_only`): the change is reported, never
  written. Every path in another repository takes this route unless it is
  `private_excluded`.
- **excluded** (`private_excluded` or `unsupported`): the file is never read,
  hashed or changed.

A file can hold several surfaces. Only the `hooks` key of `.claude/settings.json`,
`.codex/hooks.json` or `.codex/config.toml` goes through the hook contract; every
other key keeps its bytes. The whole-file classifier therefore reports those
files as `contract_required`.

Per-file details that the matrix does not show:

- Nested instruction files, `.claude/rules`, agent definitions, commands and
  nested skill bundles are adoptable only after the caller confirms no nested
  repository encloses the path; otherwise they keep their unconfirmed verdict.
- An executable file in an adoptable location carries the hook facet, so a
  change plan keeps it unresolved until a hook contract passes.
- A `SKILL.md` or agent definition whose frontmatter can register hooks needs the
  hook contract, decided from its bytes when the change is staged.
- `CLAUDE.local.md`, `.claude/settings.local.json`, `.git` and `__pycache__` are
  `private_excluded`.

## Deferred surfaces

Change plans list what they leave for later in `deferred_surfaces`: the
controller's own pending work first, then `surfaces.deferred(resolved)`, every
surface not resolved by that plan except one that every runtime excludes. Memory
is excluded for both runtimes, so it is never deferred.

- `ahl improve .` (behavioral candidate): resolves the surfaces some runtime
  routes to adopt (`surfaces.adopted()`: `instructions`, `path_rules`,
  `commands_skills`) and defers `hooks`, `workflows`, `agents`, `plugins`, `mcp`,
  `output_styles` and `settings`.
- `ahl improve . --repair-contracts`: resolves only skill headers
  (`commands_skills`) and defers every other surface. It also resolves `hooks`
  when the run has hook units and every hook contract passed.

## Version pins

The research is pinned to Claude Code 2.1.317 and Codex 0.166.0 (source
`openai/codex@d786ff91`), retrieved on 2026-11-05. Each `surfaces.CAPABILITIES`
row cites public documentation under `https://code.claude.com/docs/en/` or
`https://developers.openai.com/codex/`, or the pinned Codex source tree. An
inventory for any other version is `unsupported`. `verified` means documented at
the pin; it never means AHL observed a runtime loading the surface.

Version-gated Claude Code items: `AGENTS.md` read only without a `CLAUDE.md`
(>=2.1.277), project rules skipped without the project setting source
(>=2.1.211), hook `onFailure` (>=2.1.295), `/workflow-authoring` (>=2.1.248),
`CLAUDE_CODE_WORKFLOW_MAX_CONCURRENT_AGENTS` (>=2.1.269), agent `omitClaudeMd`
(>=2.1.271) and `autoCompactWindow` (>=2.1.296), `claude plugin validate`
(>=2.1.233) and `managedMcpServers` (>=2.1.259).

Unverified items stay labelled `unverified`; do not rely on them as facts:

- Claude Code: subdirectory and rule file size limit; `claudeMdExcludes` glob
  matching; launch loading of rules without `paths`; `paths` glob dotfile, case,
  bracket and negation semantics; the absence of a static workflow validator; the
  exact plugin cache subpath; managed settings OS paths; `--setting-sources
  project`; `--strict-mcp-config` with an explicit `--mcp-config`;
  `disableAllHooks` through `--settings`.
- Codex: hook feature version gates; agent role load timing; removal of
  deprecated custom prompts after 0.166.0; the 1024-character skill description
  limit; plugin agents (absence not proven); a plugin validate command; the
  memories layout; a temporary `CODEX_HOME` with model, effort and one workspace
  trust entry.
