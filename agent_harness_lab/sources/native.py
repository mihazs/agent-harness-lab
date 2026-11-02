"""Shared native snapshot admission, revision history and declared execution receipts."""
from datetime import datetime, timezone
import time
from ..evaluation.integrity import contained
from ..telemetry.native import MAX_BYTES, correlated
from .execution import CoverageManifest, ExecutionIdentity, ExecutionMetadata, IDENTITY_FIELDS, IdentityField, MissingField, RecordRef, ScanBound
from .schema import EvidenceRecord, digest
from .snapshot import LocalExport, Snapshot
from .graph import EvidenceGraph, JoinReceipt


class NativeCapture(LocalExport):
    def select(self, source, fields):
        descriptor, selected = self.selection(source)
        if (not isinstance(selected, dict) or set(selected) != fields
                or type(selected['schema_version']) is not int or selected['schema_version'] != 1):
            raise ValueError('invalid native capture selection')
        if digest(selected) != source.scope_sha256: raise ValueError('native selection scope mismatch')
        project = contained(self.manifest.parent, selected['project'])
        if not project.is_dir(): raise ValueError('native project must be an existing declared directory')
        limit = selected['max_records']
        if type(limit) is not int or not 1 <= limit <= 10000: raise ValueError('invalid native source record limit')
        return descriptor, selected, project, limit

    def file(self, source, descriptor, selected, project, decoder):
        from ..config import supported
        if not supported(): raise NotImplementedError('native capture requires safe filesystem APIs')
        from .git_view import Folder
        path = contained(self.manifest.parent, selected['capture'])
        revisions = {}
        with Folder(path.parent, time.monotonic()+10) as folder:
            rows, summary, sha256 = decoder(folder, path.name, project, selected['max_records'], revisions=revisions)
            events = tuple(correlated(rows, folder.check))
            snapshot = self.snapshot(source, descriptor, selected, project, events, summary, sha256, revisions, folder)
            folder.verify()
            return snapshot

    def join(self, left, right, relation):
        return JoinReceipt(left, right, relation, self.format+'.native', self.format+'-native-links-v1',
            (left,) if right is None or right == left else (left, right), basis='native_fields',
            status='candidate' if right is not None else 'unlinked', reason='not_validated' if right is not None else 'missing_identity')

    def snapshot(self, source, descriptor, selected, project, events, summary, sha256, revisions, folder):
        limit = selected['max_records']
        if len(events) > 10000: raise ValueError('native source event limit exceeded')
        scan = summary.get('discovery', summary)
        files = scan.get('accepted_files', 1)
        ready = bool(files) and not (summary['partial_tail'] or scan.get('reached_limits')
            or scan.get('unsupported_headers') or scan.get('unsafe_entries_skipped'))
        records, identities, subjects = [], [], set()
        now = datetime.now(timezone.utc).isoformat()
        for event in events:
            folder.check()
            key = (event['case_id'], event['event_id'])
            native = key in revisions
            revision = revisions[key] if native else digest({k: v for k, v in event.items() if k not in ('event_id', 'capture_order')})
            if native: ident = digest((self.format+'-event-v1', *key))
            elif self.format == 'codex' or event['capture_status'] == 'unmatched_call':
                ident = digest((self.format+'-derived-coverage-v1', event['case_id'],
                    event.get('call_id'), event['capture_status'], event.get('missing_reason')))
            else: ident = digest((self.format+'-derived-event-v1', *key))
            record = EvidenceRecord(source.source_key, ident, revision, revision,
                'behavioral_event', self.format+'_'+event['activity'], now, original_at=event.get('timestamp'),
                provenance='native_field' if native else 'unknown', capture_completeness='partial')
            records.append(record)
            identity = self.identity(event, summary) if native else None
            if identity is None or identity[0] in subjects: continue
            subjects.add(identity[0])
            if len(identities) >= 1000: raise ValueError('native execution-metadata subject limit exceeded')
            ref = RecordRef(source.source_key, record.native_id_sha256, record.revision_sha256)
            fields = []
            def claim(name, value, namespace, provenance='native_field'):
                fields.append(IdentityField(name, namespace, value, 'declared', provenance, (ref,),
                    reason='source_declaration' if provenance == 'source_declared' else 'native_metadata', observed_at=event.get('timestamp')))
            claim('project', source.project_sha256, 'ahl.project', 'source_declared')
            claim('worktree', digest(str(project)), self.format+'.declared_cwd', 'source_declared')
            for name, value, namespace in identity[1]: claim(name, value, namespace)
            identities.append(ExecutionIdentity(ref, tuple(fields)))
        missing = tuple(MissingField(name, 'no_effective_binding') for name in IDENTITY_FIELDS
            if not identities or any(not any(f.name == name for f in identity.fields) for identity in identities))
        coverage = CoverageManifest(source.source_key, source.scope_sha256, source.scope_sha256, source.version, self.parser_versions[source.version],
            (ScanBound('records', limit, scan['input_records']), ScanBound('files', selected.get('max_files', 1), files), ScanBound('bytes', MAX_BYTES, folder.bytes)),
            missing, gaps=('not_captured', 'no_effective_binding'), reported_completeness='partial',
            limit_reached=scan['input_records'] == limit or bool(scan.get('reached_limits')),
            truncated=summary['partial_tail'] or bool(scan.get('reached_limits')))
        metadata = ExecutionMetadata(tuple(identities), coverage)
        metadata.validate(source, records)
        graph = relationships(self, source, events, records, revisions, folder.check)
        graph.validate(source, records)
        snapshot = Snapshot(sha256, tuple(records), metadata, graph, checkpoint_ready=ready)
        folder.check()
        self.verify_selection(source, descriptor, selected)
        self.summary = dict(summary, checkpoint_ready=ready, source_identity='caller_declared')
        return snapshot



def relationships(connector, source, events, records, revisions, check):
    pairs, nodes, groups = [], [], {}
    for event, record in zip(events, records):
        check()
        ref = RecordRef(record.source_sha256, record.native_id_sha256, record.revision_sha256)
        nodes.append(ref)
        if (event['case_id'], event['event_id']) not in revisions: continue
        pairs.append((event, ref))
        if event['activity'] in ('tool_call', 'tool_result') and event.get('call_id') is not None:
            key = (event['case_id'], event['call_id'])
            groups.setdefault(key, {}).setdefault(event['activity'], []).append((event, ref))
    joins = connector.links(pairs, check)
    for group in groups.values():
        check()
        calls, results = group.get('tool_call', ()), group.get('tool_result', ())
        if len(calls) == len(results) == 1 and results[0][0]['capture_status'] == 'linked':
            joins.append(connector.join(results[0][1], calls[0][1], 'tool_result_of'))
    return EvidenceGraph(source.source_key, source.project_sha256, source.scope_sha256, source.version, tuple(nodes), tuple(joins))
