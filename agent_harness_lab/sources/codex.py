"""Pinned Codex native capture selection and field mapping."""
from ..evaluation.integrity import contained
from ..telemetry.codex_rollout import decode, text
from .native import NativeCapture
from .schema import digest

SOURCE_VERSION = '0.166.0-v1'
ROOT_VERSION = '0.166.0-v2'
PARSER_VERSION = 'codex-native-journal-v1'


class CodexCapture(NativeCapture):
    format = 'codex'
    parser_versions = {SOURCE_VERSION: PARSER_VERSION, ROOT_VERSION: 'codex-native-root-journal-v1'}

    def read(self, source):
        if (source.kind, source.provider) != ('local_file', 'codex-rollout') or source.version not in (SOURCE_VERSION, ROOT_VERSION):
            raise ValueError('unsupported native Codex source version')
        fields = {'schema_version', 'project', 'capture', 'max_records'} if source.version == SOURCE_VERSION else {
            'schema_version', 'project', 'source_root', 'session', 'max_files', 'max_scan_files', 'max_records'}
        descriptor, selected, project, limit = self.select(source, fields)
        revisions = {}
        if source.version == ROOT_VERSION:
            from ..telemetry.codex_discovery import capture
            root = contained(self.manifest.parent, selected['source_root'])
            for name, maximum in (('max_files', 100), ('max_scan_files', 2000)):
                if type(selected[name]) is not int or not 1 <= selected[name] <= maximum:
                    raise ValueError('invalid native root limit')
            if selected['session'] is not None: text(selected['session'])
            with capture(root, project, session=selected['session'], max_files=selected['max_files'],
                    max_scan_files=selected['max_scan_files'], max_records=limit, revisions=revisions) as (events, summary, sha256, folder):
                return self.snapshot(source, descriptor, selected, project, events, summary, sha256, revisions, folder)
        return self.file(source, descriptor, selected, project, decode)

    def identity(self, event, summary):
        if event['activity'] not in ('session_start', 'turn_context'): return None
        fields = [('session', event['case_id'], 'codex.thread'), ('runtime', digest('codex'), 'codex.producer'),
            ('runtime_version', digest('0.166.0'), 'codex.producer_version')]
        if event.get('native_parent_thread_fingerprint') is not None:
            fields.append(('parent_session', event['native_parent_thread_fingerprint'], 'codex.thread'))
        if event.get('requested_model_fingerprint') is not None:
            fields.append(('requested_model', event['requested_model_fingerprint'], 'codex.requested_model'))
        return (event['case_id'], event['event_id']), fields

    def links(self, pairs, check):
        headers = {event['case_id']: ref for event, ref in pairs if event['activity'] == 'session_start'}
        joins = []
        for event, ref in pairs:
            check(); header = headers[event['case_id']]
            if ref != header: joins.append(self.join(ref, header, 'native_session_member'))
        return joins
