"""Narrow lexical pre-tool hook candidates; handler behavior remains unknown."""
from agent_harness_lab.audit import _views
from agent_harness_lab.sources.schema import digest

# The inventory stores matcher digests, not raw matcher text. Only these exact
# Claude tool selectors are recognized; all other matcher expressions stay unknown.
_EDIT_MATCHERS = frozenset(digest(value) for value in ('Edit', 'Write', 'Edit|Write', 'Write|Edit'))
_CODEX_PATCH_MATCHERS = frozenset(digest(value) for value in ('apply_patch', '^apply_patch$'))


def first_write_candidate(inventory):
    """Return one narrow declared pre-tool candidate without executing handlers."""
    if inventory.status != 'declared' or inventory.runtime not in ('claude-code', 'codex'): return None
    for node in inventory.nodes:
        path = '.claude/settings.json' if inventory.runtime == 'claude-code' else '.codex/hooks.json'
        if node.origin != 'checkout' or node.path != path or node.declared_state != 'present': continue
        for kind, status, facts in _views(node, inventory.runtime):
            if kind != 'hook' or status != 'parsed': continue
            if inventory.runtime == 'claude-code' and (facts.get('disable_all_hooks_valid') is not True
                    or facts.get('disable_all_hooks') is True): continue
            for index in range(facts.get('handlers', 0)):
                prefix = f'handler.{index}.'
                if (facts.get(prefix + 'event') != 'PreToolUse' or facts.get(prefix + 'status') != 'parsed'
                        or facts.get(prefix + 'matcher_group_supported') is not True
                        or facts.get(prefix + 'audit_candidate_supported') is not True): continue
                matcher = facts.get(prefix + 'matcher_kind')
                matched = (matcher == 'all' or matcher in ('exact', 'pipe_list')
                           and facts.get(prefix + 'matcher_sha256') in _EDIT_MATCHERS) if inventory.runtime == 'claude-code' else (
                           matcher == 'regex' and facts.get(prefix + 'matcher_sha256') in _CODEX_PATCH_MATCHERS)
                if matched:
                    return dict(source_path=node.path, handler_index=index, matcher_scope='tool_name_only',
                                observed_execution='unknown', path_enforcement='unknown')
    return None
