"""Versioned local Git relationships; captured files do not prove runtime loading."""
from itertools import chain
from .execution import CoverageManifest, ExecutionMetadata, IDENTITY_FIELDS, MissingField, RecordRef, ScanBound
from .git_view import MAX_BYTES
from .graph import EvidenceGraph, JoinReceipt
from .schema import digest
from .snapshot import Snapshot


def capture(source, rows, summary, record, max_commits, bytes_read):
    commits = {c['commit_sha256']: r for c, r in zip(summary['commits'], rows)}
    references = [r for r in rows if r.record_type == 'git_ref']
    files = [r for r in rows if r.record_type == 'harness_file']
    inventories = [r for r in rows if r.record_type == 'instruction_inventory']
    edges = sum(len(c['parents_sha256']) for c in summary['commits'])+len(references)+len(files)+len(inventories)+1
    capacity = 10000-len(rows)-1
    if edges > 20000 or capacity < 0: raise ValueError('Git relationship admission budget exceeded')
    targets = {}
    for target, kind in chain(((p, 'commit') for c in summary['commits'] for p in c['parents_sha256']),
            ((r['target_sha256'], r['object_type']) for r in summary['refs'])):
        if target not in commits:
            targets[target] = kind
            if len(targets) > capacity: raise ValueError('Git reference-node admission budget exceeded')
    for target, kind in sorted(targets.items()):
        value = dict(object_sha256=target, object_type=kind, capture='reference_only')
        reference = record('git_object_reference', target, digest(value), digest(value))
        rows.append(reference); commits[target] = reference
    harness = record('harness_snapshot', 'project', summary['harness_sha256'], summary['harness_sha256'])
    rows.append(harness)
    def ref(row): return RecordRef(row.source_sha256, row.native_id_sha256, row.revision_sha256)
    joins = []
    def link(left, right, relation):
        endpoints = (ref(left), ref(right))
        joins.append(JoinReceipt(*endpoints, relation, 'git.native', 'git-claims-v1', endpoints, basis='native_fields'))
    for commit in summary['commits']:
        for parent in commit['parents_sha256']:
            link(commits[commit['commit_sha256']], commits[parent], 'commit_parent')
    for native, row in zip(summary['refs'], references):
        link(row, commits[native['target_sha256']], 'git_ref_target')
    link(harness, commits[summary['head_sha256']], 'harness_based_on')
    for row in files: link(row, harness, 'harness_member')
    for row in inventories: link(row, harness, 'instruction_inventory_of')
    graph = EvidenceGraph(source.source_key, source.project_sha256, source.scope_sha256, source.version,
        tuple(ref(r) for r in rows), tuple(joins))
    limited = 'history_limit_reached' in summary['warnings']
    shallow = 'shallow_history' in summary['warnings']
    coverage = CoverageManifest(source.source_key, source.scope_sha256, source.scope_sha256, source.version,
        'git-frozen-view-v2', (ScanBound('records', 10000, len(rows)), ScanBound('commits', max_commits, len(summary['commits'])),
        ScanBound('bytes', MAX_BYTES, bytes_read)), tuple(MissingField(n, 'no_effective_binding') for n in IDENTITY_FIELDS),
        gaps=('no_effective_binding', 'not_captured'), reported_completeness='partial', limit_reached=limited, truncated=limited or shallow)
    graph.validate(source, rows)
    return Snapshot(digest(summary), tuple(rows), ExecutionMetadata((), coverage), graph)
