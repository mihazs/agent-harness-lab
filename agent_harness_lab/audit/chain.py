"""Codex project-doc budget over the declared AGENTS.md directory chain, from inventory facts only.

Pinned to openai/codex@d786ff91 codex-rs/core/src/agents_md.rs (read_agents_md, agents_md_paths) and
codex-rs/config/src/config_toml.rs (DEFAULT_PROJECT_DOC_MAX_BYTES = 32 * 1024): from the project root to the cwd,
each directory's first existing candidate (AGENTS.override.md, then AGENTS.md) is read in order under one
project_doc_max_bytes budget. A file larger than the remaining budget is truncated to it, a non-blank file uses
what it loaded, and once the budget is exhausted every later file is skipped. Separators and the CODEX_HOME file
use no budget.

The chain assumes the default project root marker .git and no fallback names. The byte budget is the
documented 32 KiB default unless the caller explicitly declares a different value; runtime configuration,
markers and fallback lists are never read. It is settled only when the checkout root holds
.git, task_cwd is known and every scope's selection was parsed. The walk stops at the first scope it cannot settle,
such as a refused (symlinked) candidate, which Codex would follow, so only budget outcomes the earlier files
already decide are reported, and an explicitly untrusted project, which loads no project docs, reports none.
Only nonconformant outcomes are returned: an unread file or an unknown setting never becomes a pass here.
"""
from pathlib import PurePosixPath
from agent_harness_lab import surfaces
from agent_harness_lab.sources.instructions import CODEX_DEFAULT_BYTES, GLOBAL_NAMES
from agent_harness_lab.sources.schema import digest

# The pinned capability row a budget finding cites.
SOURCE = next(row.doc_url for row in surfaces.CAPABILITIES
              if row.surface == 'instructions' and row.runtime == 'codex' and row.item.startswith('project_doc_max_bytes'))
TRUNCATED = 'instruction_chain_truncated_at_default_project_doc_bytes'
SKIPPED = 'instruction_chain_skipped_at_default_project_doc_bytes'
DECLARED_TRUNCATED = 'instruction_chain_truncated_at_declared_project_doc_bytes'
DECLARED_SKIPPED = 'instruction_chain_skipped_at_declared_project_doc_bytes'


def _recover(inventory):
    """task_cwd from its digest: a checkout directory that holds or encloses a listed path, or None."""
    paths = [n.path for n in inventory.nodes if n.origin == 'checkout']
    paths += [r.path for r in inventory.refusals if r.root == 'checkout']
    candidates = {'.'} | {str(parent) for path in paths for parent in PurePosixPath(path).parents}
    return next((c for c in sorted(candidates) if digest(c) == inventory.task_cwd_sha256), None)


def codex(inventory, task_cwd=None, *, declared_limit=None):
    """{path: (reason, measures)} for chain files the declared or default budget truncates or skips."""
    if declared_limit is not None and (type(declared_limit) is not int or declared_limit < 0):
        raise ValueError('invalid declared Codex project document byte limit')
    if inventory.runtime != 'codex' or inventory.status != 'declared': return {}
    limit = CODEX_DEFAULT_BYTES if declared_limit is None else declared_limit
    truncated = TRUNCATED if declared_limit is None else DECLARED_TRUNCATED
    skipped = SKIPPED if declared_limit is None else DECLARED_SKIPPED
    if task_cwd is None: task_cwd = _recover(inventory)
    if task_cwd is None or digest(task_cwd) != inventory.task_cwd_sha256: return {}
    refused = {r.path for r in inventory.refusals if r.root == 'checkout'}
    if '.git' not in refused: return {}  # the project root lies above the checkout or nowhere: not settled here
    nodes = {n.path: n for n in inventory.nodes if n.origin == 'checkout'}
    if any(n.trust == 'declared_untrusted' for n in nodes.values()): return {}  # Codex loads no project docs
    parts = PurePosixPath(task_cwd).parts if task_cwd != '.' else ()
    scopes = [PurePosixPath(*parts[:depth]) for depth in range(len(parts) + 1)]
    remaining, found = limit, {}
    for scope in scopes:
        selected = None
        for name in GLOBAL_NAMES:
            path = str(scope / name)
            if path in refused: return found  # Codex follows links: the selection and its size are unknown here
            if path in nodes: selected = nodes[path]; break
        if selected is None: continue
        facts = dict(selected.facts)
        size, leading = facts.get('bytes'), facts.get('codex_leading_blank_bytes')
        if (selected.kind != 'instruction' or selected.declared_state != 'present' or selected.parse_status != 'parsed'
                or type(size) is not int or type(leading) is not int or not 0 <= leading <= size):
            return found
        blank = leading == size
        if remaining == 0:  # Codex stops at the first file after exhaustion; a blank one loses nothing
            if not blank: found[selected.path] = (skipped, _measures(0, size, limit))
            continue
        if size > remaining:
            # A prefix that ends inside the leading whitespace run is blank and uses no budget unless it cuts a
            # multi-byte character, which the facts do not record, so its loaded bytes and later files are unsettled.
            if remaining < leading:
                if not blank: found[selected.path] = (truncated, (('budget_bytes', limit),))
                return found
            # A prefix that is exactly the whitespace run ends on a character boundary: blank, so Codex loads none of
            # the file and the budget carries on unchanged.
            if remaining == leading:
                found[selected.path] = (truncated, _measures(0, size, limit))
                continue
            found[selected.path] = (truncated, _measures(remaining, size - remaining, limit))
            remaining = 0
        elif not blank: remaining -= size
    return found


def _measures(loaded, dropped, limit):
    return (('budget_bytes', limit), ('dropped_bytes', dropped), ('loaded_bytes', loaded))
