"""Explicit native capture journals; source declarations never certify execution."""
from datetime import datetime, timezone
import time
from ..evaluation.integrity import contained
from ..telemetry.codex_rollout import MAX_BYTES, correlated, decode
from .execution import CoverageManifest, ExecutionIdentity, ExecutionMetadata, IDENTITY_FIELDS, IdentityField, MissingField, RecordRef, ScanBound
from .schema import EvidenceRecord, digest
from .snapshot import LocalExport, Snapshot
from .graph import EvidenceGraph, JoinReceipt

SOURCE_VERSION = '0.166.0-v1'
PARSER_VERSION = 'codex-native-journal-v1'


class CodexCapture(LocalExport):
    def read(self, source):
        if (source.kind, source.provider, source.version) != ('local_file', 'codex-rollout', SOURCE_VERSION):
            raise ValueError('unsupported native Codex source version')
        descriptor, selected = self.selection(source)
        if (not isinstance(selected, dict) or set(selected) != {'schema_version', 'project', 'capture', 'max_records'}
                or type(selected['schema_version']) is not int or selected['schema_version'] != 1):
            raise ValueError('invalid native capture selection')
        if digest(selected) != source.scope_sha256: raise ValueError('native selection scope mismatch')
        project = contained(self.manifest.parent, selected['project'])
        capture = contained(self.manifest.parent, selected['capture'])
        if not project.is_dir(): raise ValueError('native project must be an existing declared directory')
        limit = selected['max_records']
        if type(limit) is not int or not 1 <= limit <= 10000: raise ValueError('invalid native source record limit')
        from ..config import supported
        if not supported(): raise NotImplementedError('native capture requires safe filesystem APIs')
        from .git_view import Folder
        revisions = {}
        with Folder(capture.parent, time.monotonic()+10) as folder:
            rows, summary, sha256 = decode(folder, capture.name, project, limit, revisions=revisions)
            events = tuple(correlated(rows, folder.check))
            if len(events) > 10000: raise ValueError('native source event limit exceeded')
            records, identities = [], []
            now = datetime.now(timezone.utc).isoformat()
            for event in events:
                folder.check()
                key = (event['case_id'], event['event_id'])
                native = key in revisions
                revision = revisions[key] if native else digest({k: v for k, v in event.items() if k not in ('event_id', 'capture_order')})
                ident = digest(('codex-event-v1', *key)) if native else digest(('codex-derived-coverage-v1', event['case_id'],
                    event.get('call_id'), event['capture_status'], event.get('missing_reason')))
                record = EvidenceRecord(source.source_key, ident, revision, revision,
                    'behavioral_event', 'codex_'+event['activity'], now, original_at=event.get('timestamp'),
                    provenance='native_field' if native else 'unknown', capture_completeness='partial')
                records.append(record)
                if event['activity'] not in ('session_start', 'turn_context'): continue
                if len(identities) >= 1000: raise ValueError('native execution-metadata subject limit exceeded')
                ref = RecordRef(source.source_key, record.native_id_sha256, record.revision_sha256)
                fields = []
                def claim(name, value, namespace, provenance='native_field'):
                    fields.append(IdentityField(name, namespace, value, 'declared', provenance, (ref,),
                        reason='source_declaration' if provenance == 'source_declared' else 'native_metadata', observed_at=event.get('timestamp')))
                claim('project', source.project_sha256, 'ahl.project', 'source_declared')
                claim('worktree', digest(str(project)), 'codex.declared_cwd', 'source_declared')
                claim('session', event['case_id'], 'codex.thread')
                claim('runtime', digest('codex'), 'codex.producer')
                claim('runtime_version', digest('0.166.0'), 'codex.producer_version')
                if event.get('native_parent_thread_fingerprint') is not None:
                    claim('parent_session', event['native_parent_thread_fingerprint'], 'codex.thread')
                if event.get('requested_model_fingerprint') is not None:
                    claim('requested_model', event['requested_model_fingerprint'], 'codex.requested_model')
                identities.append(ExecutionIdentity(ref, tuple(fields)))
            missing = tuple(MissingField(name, 'no_effective_binding') for name in IDENTITY_FIELDS
                if not identities or any(not any(f.name == name for f in identity.fields) for identity in identities))
            coverage = CoverageManifest(source.source_key, source.scope_sha256, source.scope_sha256, source.version, PARSER_VERSION,
                (ScanBound('records', limit, summary['input_records']), ScanBound('files', 1, 1), ScanBound('bytes', MAX_BYTES, folder.bytes)),
                missing, gaps=('not_captured', 'no_effective_binding'), reported_completeness='partial',
                limit_reached=summary['input_records'] == limit, truncated=summary['partial_tail'])
            metadata = ExecutionMetadata(tuple(identities), coverage)
            metadata.validate(source, records)
            graph = relationships(source, events, records, revisions, folder.check)
            graph.validate(source, records)
            snapshot = Snapshot(sha256, tuple(records), metadata, graph, checkpoint_ready=not summary['partial_tail'])
            folder.check()
            self.verify_selection(source, descriptor, selected)
            folder.verify()
            self.summary = dict(summary, checkpoint_ready=not summary['partial_tail'], source_identity='caller_declared')
            return snapshot


def relationships(source, events, records, revisions, check):
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
    header = next(ref for event, ref in pairs if event['activity'] == 'session_start')
    def join(left, right, relation):
        return JoinReceipt(left, right, relation, 'codex.native', 'codex-native-links-v1', (left, right), basis='native_fields')
    joins = []
    for event, ref in pairs:
        check()
        if ref != header: joins.append(join(ref, header, 'native_session_member'))
    for group in groups.values():
        check()
        calls, results = group.get('tool_call', ()), group.get('tool_result', ())
        if len(calls) == len(results) == 1 and results[0][0]['capture_status'] == 'linked':
            joins.append(join(results[0][1], calls[0][1], 'tool_result_of'))
    return EvidenceGraph(source.source_key, source.project_sha256, source.scope_sha256, source.version, tuple(nodes), tuple(joins))
