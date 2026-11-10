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
                target = observed.get(edge.target_sha256)
                if target is not None and target.reason in ('import_missing', 'import_depth_limit',
                        'import_target_unreadable', 'external_import_not_captured', 'non_utf8_import'):
                    reasons.add(target.reason)
    empty_import = any(edge.kind == 'imports' and edge.source_sha256 in active
                       and edge.target_sha256 in active and edge.target_sha256 in facts
                       and facts[edge.target_sha256][6] for edge in inventory.edges)
    complete = not reasons
    return dict(status='declared' if complete else 'unknown',
                bytes=sum(facts[key][1] for key in active) if complete else None,
                lines=sum(facts[key][2] for key in active) if complete else None,
                files=len(active) if complete else None,
                shape=(dict(constraints=any(facts[key][5][0] for key in active),
                            verification_commands=any(facts[key][5][1] for key in active),
                            procedure_sections=any(facts[key][5][2] for key in active)) if complete else None),
                reasons=sorted(reasons), empty_import=empty_import, scope='captured_checkout_launch_files',
                settings='caller_declared' if 'instruction_files' in profile else 'assumed_default',
                observed_loading='unknown', snapshot_relation='separate_resolver_observation',
                inventory_comparison='recognized_instruction_files_only',
                cross_observation_consistency='unknown', publication_allowed=False)


def codex(profile, folder, checkout, inventory, *, declared_limit=None):
    """Count the selected checkout chain only when all bytes are settled and untruncated."""
    import hashlib
    from pathlib import Path
    from agent_harness_lab.audit import chain
    from agent_harness_lab.sources import instruction_shapes

    if profile.get('runtime') != 'codex' or inventory.runtime != 'codex':
        raise ValueError('Codex profile and inventory required')
    if declared_limit is not None and (type(declared_limit) is not int or declared_limit < 0):
        raise ValueError('invalid declared Codex project document byte limit')
    parts = PurePosixPath(profile['task_cwd']).parts if profile['task_cwd'] != '.' else ()
    scopes = [PurePosixPath(*parts[:depth]) for depth in range(len(parts) + 1)]
    nodes = {n.path: n for n in inventory.nodes if n.origin == 'checkout' and n.kind == 'instruction'}
    refused = {r.path for r in inventory.refusals if r.root == 'checkout'}
    reasons, rows = set(), []
    if digest(profile['task_cwd']) != inventory.task_cwd_sha256:
        raise ValueError('task directory differs from the inventory')
    if any(node.trust == 'declared_untrusted' for node in nodes.values()):
        reasons.add('project_declared_untrusted')
    if inventory.status != 'declared' or '.git' not in refused or folder.inspect(str(Path(checkout) / '.git')) is None:
        reasons.add('project_root_unsettled')
    for scope in scopes:
        if str(scope) != '.' and folder.inspect(str(Path(checkout) / scope / '.git')) is not None:
            reasons.add('nested_project_root_unsettled')
        for name in instructions.GLOBAL_NAMES:
            path = str(scope / name)
            if path in refused:
                reasons.add('instruction_candidate_refused')
                break
            raw = folder.read(str(Path(checkout) / path), 4 * 1024 * 1024)
            node = nodes.get(path)
            if raw is None:
                if node is not None: reasons.add('inventory_content_changed')
                continue
            if node is None or node.content_sha256 != hashlib.sha256(raw).hexdigest() or node.parse_status != 'parsed':
                reasons.add('inventory_content_changed')
                break
            text = raw.decode('utf-8')
            if not instructions.codex_blank(raw):
                rows.append((len(raw), len(text.splitlines()), instruction_shapes.analyze(text)))
            break
    folder.verify()
    if chain.codex(inventory, profile['task_cwd'], declared_limit=declared_limit):
        reasons.add('truncated_chain_not_counted')
    complete = not reasons
    return dict(status='declared' if complete else 'unknown',
                bytes=sum(row[0] for row in rows) if complete else None,
                lines=sum(row[1] for row in rows) if complete else None,
                files=len(rows) if complete else None,
                shape=(dict(constraints=any(row[2][0] for row in rows),
                            verification_commands=any(row[2][1] for row in rows),
                            procedure_sections=any(row[2][2] for row in rows)) if complete else None),
                reasons=sorted(reasons), scope='captured_checkout_project_chain',
                settings='caller_declared_budget' if declared_limit is not None else 'assumed_default',
                observed_loading='unknown', snapshot_relation='separate_resolver_observation',
                inventory_comparison='selected_instruction_files_only',
                cross_observation_consistency='unknown', publication_allowed=False)
