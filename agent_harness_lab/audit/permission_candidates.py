"""Exact lexical deny candidates, not a claim of runtime enforcement."""
from agent_harness_lab.evaluation.integrity import relative
from agent_harness_lab.sources import harness_inventory
from agent_harness_lab.sources.schema import digest

PROFILE = 'ahl-permission-candidates-v1'


def report(inventory, protected):
    """Compare private protected-path metadata to hashed Claude project deny entries.

    Only exact Edit/Write forms are compared; an absent match is always unknown.
    The inputs are independent snapshots, not proof of current runtime behavior.
    """
    if type(inventory) is not harness_inventory.HarnessInventory:
        raise ValueError('harness inventory required')
    if (not isinstance(protected, dict) or protected.get('profile') != 'ahl-protected-path-candidates-v1'
            or protected.get('publication_allowed') is not False or protected.get('status') not in ('completed', 'blocked')
            or not isinstance(protected.get('paths'), list) or protected['status'] == 'blocked' and protected['paths']):
        raise ValueError('private protected-path inventory required')
    hashes = set()
    if inventory.status == 'declared' and inventory.runtime == 'claude-code':
        for node in inventory.nodes:
            if node.origin != 'checkout' or node.path != '.claude/settings.json' or node.declared_state != 'present':
                continue
            facts = dict(node.facts)
            if facts.get('config.parse_status') != 'parsed' or facts.get('config.permission_deny_state') != 'parsed':
                continue
            hashes.update(value for key, value in facts.items() if key.startswith('config.permission_deny.') and isinstance(value, str))
    rows = []
    for row in protected['paths']:
        if not isinstance(row, dict) or not isinstance(row.get('path'), str):
            raise ValueError('invalid protected-path candidate')
        path = row['path']
        relative(path)
        candidates = {digest(('ahl-permission-deny-v1', f'{tool}({prefix}{path})'))
                      for tool in ('Edit', 'Write') for prefix in ('', './')}
        matched = sorted(hashes & candidates)
        rows.append(dict(path=path, status='permission_candidate_present' if matched else 'unknown',
                         matched_rule_sha256=matched, enforcement='unknown'))
    return dict(profile=PROFILE, paths=rows, matching='exact_lexical_only', coverage='unknown',
                effective='unknown', publication_allowed=False)
