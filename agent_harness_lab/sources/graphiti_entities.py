"""Offline Graphiti entity-node projection; matching identities are not verified links."""
from datetime import datetime, timezone
import hashlib

from agent_harness_lab.evaluation.integrity import regular_equal
from .execution import CoverageManifest, ExecutionMetadata, ScanBound
from .graphiti_episodes import GraphitiEpisodes, JSON_BOUNDS, MAX_BYTES, MAX_EPISODES, _identity
from .hardened import load_json
from .schema import EvidenceRecord, digest, timestamp
from .snapshot import Snapshot

FIELDS = {'uuid', 'name', 'group_id', 'labels', 'created_at', 'name_embedding', 'summary', 'attributes'}


class GraphitiEntities(GraphitiEpisodes):
    """Project a caller-supplied array of pinned Graphiti EntityNode JSON objects."""

    def read(self, source):
        project, group, declaration, path, raw = self._input(source, 'graphiti-entity-nodes')
        try:
            rows = load_json(raw, JSON_BOUNDS, 'Graphiti entity dump')
        except (UnicodeError, ValueError, RecursionError):
            raise ValueError('invalid Graphiti entity dump') from None
        if not isinstance(rows, list) or len(rows) > MAX_EPISODES:
            raise ValueError('bounded Graphiti entity list required')
        now = datetime.now(timezone.utc).isoformat()
        seen, records = set(), []
        for row in rows:
            if not isinstance(row, dict) or set(row) != FIELDS or row['group_id'] != group:
                raise ValueError('unsupported Graphiti entity or foreign group')
            native = _identity(row['uuid'])
            if native in seen: raise ValueError('duplicate Graphiti entity identity')
            seen.add(native)
            if (not isinstance(row['name'], str) or not isinstance(row['summary'], str) or not isinstance(row['attributes'], dict)
                    or not isinstance(row['labels'], list) or not all(isinstance(label, str) for label in row['labels'])
                    or row['name_embedding'] is not None and not isinstance(row['name_embedding'], list)):
                raise ValueError('invalid Graphiti entity fields')
            timestamp(row['created_at'])
            revision = digest(row)
            records.append(EvidenceRecord(source.source_key, digest(('graphiti.entity', group, native)), revision, revision,
                                          'contextual_assertion', 'graphiti_entity', now, original_at=row['created_at'],
                                          provenance='native_field', capture_completeness='partial'))
        coverage = CoverageManifest(source.source_key, source.scope_sha256, None, source.version, 'graphiti-entities-v1',
                                    (ScanBound('records', MAX_EPISODES, len(records)), ScanBound('bytes', MAX_BYTES, len(raw))),
                                    gaps=('not_captured', 'no_effective_binding'), reported_completeness='partial')
        if ((project, group) != (self.project, self.group) or not regular_equal(path, raw, MAX_BYTES)
                or self.declaration(project_sha256=source.project_sha256, scope_sha256=source.scope_sha256) != (source, declaration)):
            raise ValueError('Graphiti dump or selection changed during capture')
        return Snapshot(digest((source.source_key, hashlib.sha256(raw).hexdigest())), tuple(records), ExecutionMetadata((), coverage))
