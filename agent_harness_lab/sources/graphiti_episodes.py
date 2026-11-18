"""Offline projection of caller-supplied Graphiti MCP episode responses; no provider connection."""
from datetime import datetime, timezone
import hashlib
import json

from agent_harness_lab.evaluation.integrity import read_regular, regular_equal, unique_object
from .execution import CoverageManifest, ExecutionMetadata, ScanBound
from .schema import EvidenceRecord, digest, timestamp
from .snapshot import LocalExport, Snapshot

MAX_BYTES = 2 * 1024 * 1024
MAX_EPISODES = 5000
FIELDS = {'uuid', 'name', 'content', 'created_at', 'source', 'source_description', 'group_id'}


def _identity(value):
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError('bounded Graphiti identity required')
    try:
        value.encode('utf-8')
    except UnicodeError:
        raise ValueError('invalid Graphiti identity') from None
    return value


def _invalid_constant(_value):
    raise ValueError('nonfinite JSON value')


class GraphitiEpisodes(LocalExport):
    """One operator-selected response from the pinned Graphiti MCP `get_episodes` shape."""

    def __init__(self, manifest, *, project, group):
        super().__init__(manifest)
        self.project, self.group = _identity(project), _identity(group)

    def read(self, source):
        project, group = self.project, self.group
        if ((source.kind, source.provider, source.version) != ('local_file', 'graphiti-mcp-episodes', 'v0.31.2')
                or source.project_sha256 != digest(('graphiti.project', project))
                or source.scope_sha256 != digest(('graphiti.group', group))):
            raise ValueError('unsupported Graphiti version or selected scope')
        declaration, path = self._normalized_payload(source)
        raw = read_regular(path, MAX_BYTES)
        try:
            response = json.loads(raw, object_pairs_hook=unique_object, parse_constant=_invalid_constant)
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise ValueError('invalid Graphiti response') from exc
        if (not isinstance(response, dict) or set(response) != {'message', 'episodes'} or not isinstance(response['message'], str)
                or not isinstance(response['episodes'], list) or len(response['episodes']) > MAX_EPISODES):
            raise ValueError('unsupported Graphiti episode response')
        now = datetime.now(timezone.utc).isoformat()
        seen, records = set(), []
        for row in response['episodes']:
            if not isinstance(row, dict) or set(row) != FIELDS or row['group_id'] != group:
                raise ValueError('unsupported Graphiti episode or foreign group')
            if any(not isinstance(row[name], str) for name in ('name', 'content', 'source', 'source_description')):
                raise ValueError('invalid Graphiti episode fields')
            native = _identity(row['uuid'])
            if native in seen: raise ValueError('duplicate Graphiti episode identity')
            seen.add(native)
            if row['created_at'] is not None: timestamp(row['created_at'])
            content = digest(row)
            records.append(EvidenceRecord(source.source_key, digest(('graphiti.episode', group, native)), content, content,
                                          'contextual_assertion', 'graphiti_episode', now, original_at=row['created_at'],
                                          provenance='native_field', capture_completeness='partial'))
        coverage = CoverageManifest(source.source_key, source.scope_sha256, None, source.version, 'graphiti-episodes-v1',
                                    (ScanBound('records', MAX_EPISODES, len(records)), ScanBound('bytes', MAX_BYTES, len(raw))),
                                    gaps=('not_captured', 'no_effective_binding'), reported_completeness='partial')
        metadata = ExecutionMetadata((), coverage)
        if ((project, group) != (self.project, self.group) or not regular_equal(path, raw, MAX_BYTES)
                or self.declaration(project_sha256=source.project_sha256, scope_sha256=source.scope_sha256) != (source, declaration)):
            raise ValueError('Graphiti response or selection changed during capture')
        return Snapshot(digest((source.source_key, hashlib.sha256(raw).hexdigest())), tuple(records), metadata)
