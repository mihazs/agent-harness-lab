"""One explicit, pinned OpenCode session export; metadata claims only, never database access."""
from dataclasses import dataclass
import math
from agent_harness_lab.evaluation.integrity import contained, read_regular, regular_sha256, relative
from .hardened import HostileReport, limits, load_json
from .schema import digest, fingerprint, label
from .snapshot import LocalExport, claim_snapshot

PROVIDER = 'opencode-export'
SOURCE_VERSION = 'v1'
# Upstream session export handler and synthetic transfer contract at this source commit.
PIN = '98e4b5343e33aafded479b9ff71c12fd760d9d87'
LIMITS = dict(max_export_bytes=32*1024*1024, max_messages=100000, max_json_depth=32,
              max_json_containers=1000000, max_json_values=4000000)
MESSAGE_KINDS = frozenset(('agent-switched', 'model-switched', 'location-switched', 'user', 'synthetic',
    'system', 'skill', 'shell', 'assistant', 'compaction', 'idle'))
MAX_MESSAGE_CLAIMS = 10000


@dataclass(frozen=True)
class SessionClaim:
    claim_type: str
    native_id_sha256: str
    project_sha256: str
    message_count: int
    original_at: None = None


@dataclass(frozen=True)
class SessionClaimV2:
    claim_type: str
    native_id_sha256: str
    project_sha256: str
    message_count: int
    parent_sha256: str | None
    archive_state: str
    original_at: None = None


@dataclass(frozen=True)
class MessageClaim:
    claim_type: str
    native_id_sha256: str
    session_sha256: str
    kind: str
    position: int
    original_at: None = None


class OpenCodeExport(LocalExport):
    """Reads a single selected JSON export, never a database or an OpenCode process."""

    def read(self, source):
        if (source.kind, source.provider) != ('local_file', PROVIDER) or source.version not in ('v1', 'v2', 'v3'):
            raise ValueError('unsupported OpenCode source')
        descriptor, selection = self.selection(source)
        fields = {'schema_version', 'upstream_commit', 'instance', 'export', 'session_sha256'}
        if (not isinstance(selection, dict) or set(selection)-{'limits'} != fields
                or type(selection['schema_version']) is not int or selection['schema_version'] not in (1, 2, 3)
                or source.version != f"v{selection['schema_version']}"
                or selection['upstream_commit'] != PIN or digest(selection) != source.scope_sha256):
            raise ValueError('unsupported OpenCode export selection')
        label(selection['instance']); relative(selection['export']); fingerprint(selection['session_sha256'])
        bounds = limits(selection.get('limits'), LIMITS)
        decoded = []
        def consume(position, raw, export_sha256): decoded.append(load_json(raw, bounds, 'OpenCode export'))
        export_hash, = self.capture(source, descriptor, selection, [contained(self.manifest.parent, selection['export'])],
            bounds['max_export_bytes'], consume, 'OpenCode export changed during capture', read_regular, regular_sha256)
        value = decoded.pop()
        if not isinstance(value, dict) or not isinstance(value.get('info'), dict) or not isinstance(value.get('messages'), list):
            raise ValueError('invalid OpenCode session export')
        info, messages = value['info'], value['messages']
        if len(messages) > bounds['max_messages']: raise HostileReport('OpenCode message bound exceeded')
        if source.version == 'v3' and len(messages) > MAX_MESSAGE_CLAIMS: raise HostileReport('OpenCode message claim bound exceeded')
        ids = set()
        for message in messages:
            if (not isinstance(message, dict) or not isinstance(message.get('id'), str)
                    or not message['id'] or len(message['id']) > 256 or message['id'] in ids):
                raise ValueError('OpenCode message identity missing or duplicated')
            ids.add(message['id'])
        session, project = info.get('id'), info.get('projectID')
        if (not isinstance(session, str) or not session or len(session) > 256 or
                not isinstance(project, str) or not project or project == 'global' or len(project) > 256):
            raise ValueError('OpenCode session or project identity missing')
        if source.version == 'v3' and (not session.startswith('ses') or any(c.isspace() for c in session)):
            raise ValueError('invalid OpenCode session claim identity')
        instance = selection['instance']
        native = digest(('opencode.session', instance, session))
        project_hash = digest(('opencode.project', instance, project))
        if native != selection['session_sha256'] or project_hash != source.project_sha256:
            raise ValueError('OpenCode export is outside the selected identity')
        # The producer version and completeness cannot be verified from this export.
        self.claims = [SessionClaim('session', native, project_hash, len(messages))]
        if source.version in ('v2', 'v3'):
            parent = info.get('parentID')
            if 'parentID' in info and (not isinstance(parent, str) or not parent.startswith('ses')
                    or any(char.isspace() for char in parent) or len(parent) > 256 or parent == session):
                raise ValueError('invalid OpenCode parent claim')
            time = info.get('time')
            if 'time' in info and not isinstance(time, dict): raise ValueError('invalid OpenCode time claim')
            archived = time.get('archived') if time is not None else None
            if time is not None and 'archived' in time and (type(archived) not in (int, float)
                    or abs(archived) > 8640000000000000 or not math.isfinite(archived)):
                raise ValueError('invalid OpenCode archive claim')
            parent_hash = digest(('opencode.session', instance, parent)) if parent is not None else None
            self.claims = [SessionClaimV2('session', native, project_hash, len(messages), parent_hash,
                'reported_archived' if archived is not None else 'unknown')]
        if source.version == 'v3':
            for position, message in enumerate(messages):
                msg_id = message['id']
                if not msg_id.startswith('msg_') or any(c.isspace() for c in msg_id):
                    raise ValueError('invalid OpenCode message claim identity')
                kind = message.get('type')
                kind = kind if isinstance(kind, str) and kind in MESSAGE_KINDS else 'unknown'
                self.claims.append(MessageClaim('message', digest(('opencode.message', instance, session, msg_id)),
                    native, kind, position))
        self.summary = dict(schema_version=1, provider=PROVIDER, source_version=source.version,
            selection_profile='pinned_export_shape', producer_version='unverified', capture_completeness='unknown',
            source_identity='source_declared_unverified', message_count=len(messages), task_outcomes_verified=False,
            warnings=['export_version_unverified', 'message_schema_unvalidated', 'messages_not_interpreted',
                      'lineage_unknown', 'completeness_unknown'])
        if source.version in ('v2', 'v3'):
            self.summary.update(parent_sha256=parent_hash, archive_state=self.claims[0].archive_state)
        if source.version == 'v3':
            self.summary['warnings'].remove('messages_not_interpreted')
            self.summary['warnings'].append('message_contents_not_interpreted')
        return claim_snapshot(source, 'opencode_', self.claims, lambda c: 'native_field',
            digest(('opencode.export', instance, selection['export'])), export_hash, self.summary)
