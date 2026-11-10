"""Narrow lexical pre-tool hook candidates; handler behavior remains unknown."""
from agent_harness_lab.audit import _views
from agent_harness_lab.sources.schema import digest

# The inventory stores matcher digests, not raw matcher text. Only these exact
# Claude tool selectors are recognized; all other matcher expressions stay unknown.
_EDIT_MATCHERS = frozenset(digest(value) for value in ('Edit', 'Write', 'Edit|Write', 'Write|Edit'))


def first_write_candidate(inventory):
    """Return one declared Claude pre-tool candidate without executing or inspecting handlers."""
    if inventory.status != 'declared' or inventory.runtime != 'claude-code': return None
    for node in inventory.nodes:
        if node.origin != 'checkout' or node.path != '.claude/settings.json' or node.declared_state != 'present': continue
        for kind, status, facts in _views(node, inventory.runtime):
            if (kind != 'hook' or status != 'parsed' or facts.get('disable_all_hooks_valid') is not True
                    or facts.get('disable_all_hooks') is True): continue
            for index in range(facts.get('handlers', 0)):
                prefix = f'handler.{index}.'
                if (facts.get(prefix + 'event') != 'PreToolUse' or facts.get(prefix + 'status') != 'parsed'
                        or facts.get(prefix + 'matcher_group_supported') is not True
                        or facts.get(prefix + 'audit_candidate_supported') is not True): continue
                matcher = facts.get(prefix + 'matcher_kind')
                if matcher == 'all' or matcher in ('exact', 'pipe_list') and facts.get(prefix + 'matcher_sha256') in _EDIT_MATCHERS:
                    return dict(source_path=node.path, handler_index=index, matcher_scope='tool_name_only',
                                observed_execution='unknown', path_enforcement='unknown')
    return None
