"""Offline local Git ref movement between two cached checkpoints; never a force-push verdict."""
from .execution import ExecutionMetadata
from .git import reference
from .graph import EvidenceGraph
from .schema import EvidenceRecord, SourceSpec, digest, fingerprint

PROFILE = 'git-ref-movement-v1'
RESOLVER_VERSION = 'git-ref-movement-v1'
HEAD = 'HEAD'


def _canonical(node, kinds):
    """One object identity for a commit record and its reference-only alias."""
    kind = kinds.get(node.key)
    if kind == 'git_commit': return digest(('git_object_reference', node.revision_sha256))
    if kind == 'git_object_reference': return node.native_id_sha256
    return None


def _target(graph, key, ref_name):
    """The captured target of one ref, or of HEAD through the harness snapshot base edge."""
    if ref_name == HEAD:
        relation, native = 'harness_based_on', digest(('harness_snapshot', 'project'))
    else:
        relation, native = 'git_ref_target', digest(('git_ref', ref_name))
    found = {j.right for j in graph.joins if j.relation == relation and j.right is not None
        and (j.left.source_sha256, j.left.native_id_sha256) == (key, native)}
    if len(found) > 1: raise ValueError('ambiguous captured Git ref target')
    return next(iter(found), None)


def _side(state, key, cursor):
    envelope = state['captures'].get(key, {}).get(cursor)
    graph = EvidenceGraph.parse(envelope['graph']) if envelope is not None and envelope['graph'] is not None else None
    metadata = envelope['metadata'] if envelope is not None else None
    coverage = ExecutionMetadata.parse(metadata).coverage if metadata is not None else None
    truncated = None if coverage is None else bool(coverage.truncated)
    return dict(checkpoint_sha256=cursor, graph=graph, capture_truncated=truncated)


def _ancestry(graph, start, kinds):
    """Canonical identities of captured ancestors through native parent edges, and walk completeness."""
    parents = {}
    for join in graph.joins:
        if join.relation == 'commit_parent' and join.right is not None:
            parents.setdefault(join.left.key, []).append(join.right)
    seen, complete, pending = set(), True, [start]
    while pending:
        node = pending.pop()
        identity = _canonical(node, kinds)
        if identity in seen: continue
        seen.add(identity)
        if kinds.get(node.key) != 'git_commit': complete = False; continue
        pending.extend(parents.get(node.key, ()))
    return seen, complete


def _classify(before, after, kinds, key, ref_name):
    if before['graph'] is None or after['graph'] is None: return 'unknown', 'checkpoint_graph_unavailable'
    old, new = _target(before['graph'], key, ref_name), _target(after['graph'], key, ref_name)
    if old is None and new is None: return 'unobserved', 'ref_not_captured'
    if old is None: return 'appeared', 'absent_from_earlier_checkpoint'
    if new is None: return 'disappeared', 'absent_from_later_checkpoint'
    earlier = _canonical(old, kinds)
    if earlier is None or _canonical(new, kinds) is None: return 'unknown', 'target_record_unavailable'
    if earlier == _canonical(new, kinds): return 'unchanged', 'same_object_identity'
    if kinds.get(new.key) != 'git_commit': return 'unknown', 'later_target_history_not_captured'
    ancestors, complete = _ancestry(after['graph'], new, kinds)
    if earlier in ancestors: return 'fast_forward', 'earlier_target_is_captured_ancestor'
    if not complete: return 'unknown', 'later_ancestry_incomplete'
    if kinds.get(old.key) != 'git_commit': return 'unknown', 'earlier_target_not_captured_commit'
    return 'non_fast_forward', 'earlier_target_absent_from_complete_captured_ancestry'


def ref_movement(source, store, *, ref_name, before=None, after=None):
    """Classify how one ref (or HEAD) moved between two checkpoints of one local Git journal.

    Ancestry comes only from captured native parent edges. A non-fast-forward move is
    an observation; it does not distinguish force-push, reset, rebase, squash or
    recreation, and absence from a checkpoint never proves creation or deletion.
    """
    if not isinstance(source, SourceSpec) or (source.kind, source.provider, source.version) != ('local_git', 'git', 'v2'):
        raise ValueError('ref movement requires a local_git/git/v2 source')
    if not isinstance(ref_name, str) or (ref_name != HEAD and reference(ref_name) != ref_name):
        raise ValueError('invalid Git reference')
    store._bind(source)
    state, key = store._read(), source.source_key
    cursors = state['cursors'].get(key) or []
    for cursor in (before, after):
        if cursor is None: continue
        fingerprint(cursor)
        if cursor not in cursors: raise ValueError('checkpoint is not in the selected Git journal')
    after = after if after is not None else (cursors[-1] if cursors else None)
    if before is None:
        position = cursors.index(after) if after is not None else 0
        before = cursors[position-1] if position > 0 else None
    elif after is None or cursors.index(before) >= cursors.index(after):
        raise ValueError('earlier checkpoint must precede the later checkpoint')
    kinds = {}
    for entry in state['records']:
        record = EvidenceRecord.parse(entry['record'])
        if record.source_sha256 == key: kinds[(key, record.native_id_sha256, record.revision_sha256)] = record.record_type
    missing = dict(checkpoint_sha256=None, graph=None, capture_truncated=None)
    old, new = (_side(state, key, c) if c is not None else missing for c in (before, after))
    if after is None or before is None:
        status, reason = 'unknown', 'no_checkpoint' if after is None else 'no_earlier_checkpoint'
    else:
        status, reason = _classify(old, new, kinds, key, ref_name)
    sides = []
    for side in (old, new):
        target = _target(side['graph'], key, ref_name) if side['graph'] is not None else None
        sides.append(dict(checkpoint_sha256=side['checkpoint_sha256'], capture_truncated=side['capture_truncated'],
            target='unavailable' if side['graph'] is None else 'absent' if target is None
            else {'git_commit': 'captured_commit', 'git_object_reference': 'reference_only'}.get(kinds.get(target.key), 'unknown')))
    return dict(schema_version=1, profile=PROFILE, resolver_version=RESOLVER_VERSION, status=status, reason=reason,
        source_sha256=key, ref_sha256=digest(ref_name), before=sides[0], after=sides[1],
        cause='unknown', ancestry_basis='captured_native_parent_edges', repository_identity='caller_declared',
        remote_state_verified=False, intent_verified=False, independent_roots=None, task_outcomes_verified=False,
        execution_authorized=False, publication_allowed=False, source_read=False, network_used=False)
