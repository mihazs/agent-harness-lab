"""Explicit pinned Claude native journals; no account or transcript discovery."""
from ..telemetry.claude_native import decode
from .native import NativeCapture
from .schema import digest

SOURCE_VERSION = '2.1.316-v1'
PARSER_VERSION = 'claude-native-journal-v1'


class ClaudeCapture(NativeCapture):
    format = 'claude'
    parser_versions = {SOURCE_VERSION: PARSER_VERSION}

    def read(self, source):
        if (source.kind, source.provider, source.version) != ('local_file', 'claude-jsonl', SOURCE_VERSION):
            raise ValueError('unsupported native Claude source version')
        descriptor, selected, project, _ = self.select(source, {'schema_version', 'project', 'capture', 'max_records'})
        return self.file(source, descriptor, selected, project, decode)

    def identity(self, event, summary):
        fields = [('session', summary['session_sha256'], 'claude.session'),
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
