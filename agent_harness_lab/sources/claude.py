"""Explicit pinned Claude native journals; no account or transcript discovery."""
from ..evaluation.integrity import contained
from ..telemetry.claude_native import decode
from ..telemetry.native import optional_limit
from .native import NativeCapture
from .schema import digest

SOURCE_VERSION = '2.1.316-v1'
ROOT_VERSION = '2.1.316-v2'
PARSER_VERSION = 'claude-native-journal-v1'
ROOT_FIELDS = {'schema_version', 'project', 'source_roots', 'session', 'max_files', 'max_scan_files', 'max_records'}


class ClaudeCapture(NativeCapture):
    format = 'claude'
    parser_versions = {SOURCE_VERSION: PARSER_VERSION, ROOT_VERSION: 'claude-native-root-journal-v1'}

    def read(self, source):
        if (source.kind, source.provider) != ('local_file', 'claude-jsonl') or source.version not in (SOURCE_VERSION, ROOT_VERSION):
            raise ValueError('unsupported native Claude source version')
        if source.version == SOURCE_VERSION:
            descriptor, selected, project, _ = self.select(source, {'schema_version', 'project', 'capture', 'max_records'})
            return self.file(source, descriptor, selected, project, decode)
        from ..telemetry.claude_discovery import capture
        descriptor, selected, project, limit = self.select(source, ROOT_FIELDS)
        if type(selected['source_roots']) is not list or not selected['source_roots']: raise ValueError('select native Claude source roots')
        roots = [contained(self.manifest.parent, root) for root in selected['source_roots']]
        if not all(root.is_dir() for root in roots): raise ValueError('native Claude source roots must be existing declared directories')
        for name in ('max_files', 'max_scan_files'): optional_limit(selected.get(name))
        revisions = {}
        with capture(roots, project, session=selected['session'], max_files=selected.get('max_files'),
                max_scan_files=selected.get('max_scan_files'), max_records=limit, revisions=revisions) as (events, summary, sha256, folder, sessions):
            self.sessions = sessions
            return self.snapshot(source, descriptor, selected, project, events, summary, sha256, revisions, folder)

    def identity(self, event, summary):
        session = summary['session_sha256'] if 'session_sha256' in summary else self.sessions[event['case_id']]
        fields = [('session', session, 'claude.session'),
            ('runtime', digest('claude-code'), 'claude.producer'), ('runtime_version', digest('2.1.316'), 'claude.producer_version')]
        if event.get('reported_model_fingerprint') is not None:
            fields.append(('served_model', event['reported_model_fingerprint'], 'claude.reported_model'))
        return event['native_message_fingerprint'], fields

    def links(self, pairs, check):
        representatives = {}
        for event, ref in pairs:
            check(); representatives.setdefault(event['native_message_fingerprint'], (event, ref))
        joins = []
        for event, ref in pairs:
            check(); owner = representatives[event['native_message_fingerprint']][1]
            if ref != owner: joins.append(self.join(ref, owner, 'native_message_member'))
        for event, ref in representatives.values():
            check(); parent = event.get('native_parent_message_fingerprint')
            if parent is not None:
                target = representatives.get(parent)
                joins.append(self.join(ref, target[1] if target is not None else None, 'native_message_parent'))
        return joins
