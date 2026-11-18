"""Offline Graphiti entity-edge declarations; no graph links are verified."""
from dataclasses import dataclass
import hashlib
import json

from agent_harness_lab.evaluation.integrity import read_regular, regular_equal, unique_object
from .graphiti_episodes import MAX_BYTES, _identity, _invalid_constant
from .schema import digest, timestamp
from .snapshot import LocalExport, claim_snapshot

MAX_EDGES, MAX_EPISODE_REFS = 5000, 10000
FIELDS = {'uuid', 'group_id', 'source_node_uuid', 'target_node_uuid', 'created_at', 'name', 'fact',
          'fact_embedding', 'episodes', 'expired_at', 'valid_at', 'invalid_at', 'reference_time', 'attributes'}


@dataclass(frozen=True)
class EdgeClaim:
    claim_type: str
    native_id_sha256: str
    row_sha256: str
    source_node_sha256: str
    target_node_sha256: str
    episode_sha256: tuple
    valid_at: str | None
    invalid_at: str | None
    expired_at: str | None
    original_at: str | None


class GraphitiEdges(LocalExport):
    """Project a caller-supplied array of pinned Graphiti EntityEdge JSON objects."""

    def __init__(self, manifest, *, project, group):
        super().__init__(manifest)
        self.project, self.group = _identity(project), _identity(group)
        self.claims = ()

    def read(self, source):
        self.claims = ()
        project, group = self.project, self.group
        if ((source.kind, source.provider, source.version) != ('local_file', 'graphiti-entity-edges', 'v0.31.2')
                or source.project_sha256 != digest(('graphiti.project', project))
                or source.scope_sha256 != digest(('graphiti.group', group))):
            raise ValueError('unsupported Graphiti version or selected scope')
        declaration, path = self._normalized_payload(source)
        raw = read_regular(path, MAX_BYTES)
        try:
            rows = json.loads(raw, object_pairs_hook=unique_object, parse_constant=_invalid_constant)
        except (UnicodeError, ValueError, RecursionError):
            raise ValueError('invalid Graphiti edge dump') from None
        if not isinstance(rows, list) or len(rows) > MAX_EDGES: raise ValueError('bounded Graphiti edge list required')
        claims, seen, references = [], set(), 0
        for row in rows:
            if not isinstance(row, dict) or set(row) != FIELDS or row['group_id'] != group:
                raise ValueError('unsupported Graphiti edge or foreign group')
            native = _identity(row['uuid'])
            if native in seen: raise ValueError('duplicate Graphiti edge identity')
            seen.add(native)
            if (not isinstance(row['name'], str) or not isinstance(row['fact'], str) or not isinstance(row['attributes'], dict)
                    or row['fact_embedding'] is not None and not isinstance(row['fact_embedding'], list)
                    or not isinstance(row['episodes'], list)):
                raise ValueError('invalid Graphiti edge fields')
            refs = tuple(_identity(value) for value in row['episodes'])
            references += len(refs)
            if references > MAX_EPISODE_REFS or len(refs) != len(set(refs)): raise ValueError('invalid Graphiti episode references')
            for key in ('created_at', 'valid_at', 'invalid_at', 'expired_at', 'reference_time'):
                if row[key] is not None: timestamp(row[key])
            if row['created_at'] is None: raise ValueError('Graphiti creation time required')
            claims.append(EdgeClaim('edge', digest(('graphiti.edge', group, native)), digest(row),
                digest(('graphiti.entity', group, _identity(row['source_node_uuid']))),
                digest(('graphiti.entity', group, _identity(row['target_node_uuid']))),
                tuple(digest(('graphiti.episode', group, value)) for value in refs),
                row['valid_at'], row['invalid_at'], row['expired_at'], row['created_at']))
        if ((project, group) != (self.project, self.group) or not regular_equal(path, raw, MAX_BYTES)
                or self.declaration(project_sha256=source.project_sha256, scope_sha256=source.scope_sha256) != (source, declaration)):
            raise ValueError('Graphiti dump or selection changed during capture')
        summary = dict(profile='graphiti-edge-claims-v1', claims=len(claims), completeness='unknown', verified=False)
        snapshot = claim_snapshot(source, 'graphiti_', claims, lambda _claim: 'native_field',
                                  digest(('graphiti.edge_dump', group)), hashlib.sha256(raw).hexdigest(), summary)
        self.claims = tuple(claims)
        return snapshot
