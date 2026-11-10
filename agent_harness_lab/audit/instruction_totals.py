"""Private, conditional launch instruction counts from the existing bounded Claude resolver.

Counts cover only the captured checkout and are never evidence of runtime loading. Unresolved
imports and cycles make the total unknown; no file content or path is returned.
"""
from pathlib import PurePosixPath

from agent_harness_lab.sources import claude_instructions, instructions
from agent_harness_lab.sources.schema import digest


def claude(profile, folder, checkout, *, expected=None):
    """Resolve and count distinct declared launch files in one verified Folder observation."""
    if profile.get('runtime') != 'claude-code':
        raise ValueError('Claude profile required')
    facts = {}
    inventory = instructions.capture(profile, folder, checkout, _audit_facts=facts)
    folder.verify()
    active = {n.path_sha256: n for n in inventory.nodes if n.applicability == 'declared_applicable'}
    reasons = set()
    observed = {node.path_sha256: node for node in inventory.nodes}
    if expected is not None and (
            any(key not in observed or observed[key].content_sha256 != value for key, value in expected.items())
            or any(key in facts and claude_instructions.scope_of(facts[key][4]) is not None
                   and expected.get(key) != node.content_sha256 for key, node in active.items())):
        reasons.add('inventory_content_changed')
    parts = PurePosixPath(profile['task_cwd']).parts if profile['task_cwd'] != '.' else ()
    scopes = [PurePosixPath(*parts[:depth]) for depth in range(len(parts) + 1)]
    launch_scopes = {digest(str(path)) for scope in scopes for path in (scope, scope / '.claude')}
    if any(node.applicability == 'private_excluded' and node.presence != 'missing'
           and node.scope_sha256 in launch_scopes for node in inventory.nodes):
        reasons.add('private_launch_file_not_captured')
    if any(node.applicability in ('unknown', 'unsupported') and node.presence != 'missing'
           and node.scope_sha256 in launch_scopes for node in inventory.nodes):
        reasons.add('launch_file_unsettled')
    if inventory.resolution_status != 'declared':
        reasons.add('resolution_incomplete')
    if any(key not in facts or facts[key][0] != node.content_sha256 for key, node in active.items()):
        reasons.add('content_facts_unavailable')
    if any(facts.get(key, (None, None, None, False))[3] for key in active):
        reasons.add('import_context_uncertain')
    for edge in inventory.edges:
        if edge.source_sha256 in active:
            if edge.kind == 'import_cycle': reasons.add('import_cycle')
            elif edge.kind == 'imports' and edge.target_sha256 not in active:
                reasons.add('import_unresolved')
    complete = not reasons
    return dict(status='declared' if complete else 'unknown',
                bytes=sum(facts[key][1] for key in active) if complete else None,
                lines=sum(facts[key][2] for key in active) if complete else None,
                files=len(active) if complete else None,
                shape=(dict(constraints=any(facts[key][5][0] for key in active),
                            verification_commands=any(facts[key][5][1] for key in active),
                            procedure_sections=any(facts[key][5][2] for key in active)) if complete else None),
                reasons=sorted(reasons), scope='captured_checkout_launch_files',
                settings='caller_declared' if 'instruction_files' in profile else 'assumed_default',
                observed_loading='unknown', snapshot_relation='separate_resolver_observation',
                inventory_comparison='recognized_instruction_files_only',
                cross_observation_consistency='unknown', publication_allowed=False)
