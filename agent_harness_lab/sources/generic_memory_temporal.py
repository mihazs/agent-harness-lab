"""Declared validity of captured generic memories; temporal fields never prove truth."""
from .execution import RecordRef
from .generic_memory_links import _bind
from .temporal import TemporalFacet


def facets(source, snapshot, rows, *, project, namespace, fields, temporal_fields):
    """Project mapped validity fields after binding all rows to exact captured revisions.

    The capture time is the time this snapshot was collected, not proof of the first historical observation.
    Missing end fields remain unknown; only an explicit boolean marks an open-ended declaration.
    """
    names = {'valid_from', 'valid_until', 'open_ended'}
    if not isinstance(temporal_fields, dict) or set(temporal_fields) != names:
        raise ValueError('explicit temporal field mapping required')
    temporal_fields = dict(temporal_fields)
    _, selected = _bind(source, snapshot, rows, project=project, namespace=namespace, fields=fields,
                        extra_fields=tuple(temporal_fields.values()))
    result = []
    for record, row in selected:
        start = row.get(temporal_fields['valid_from'])
        end = row.get(temporal_fields['valid_until'])
        open_ended = row.get(temporal_fields['open_ended'])
        if open_ended is not None and type(open_ended) is not bool:
            raise ValueError('open-ended declaration must be boolean')
        ref = RecordRef(record.source_sha256, record.native_id_sha256, record.revision_sha256)
        result.append(TemporalFacet(ref, recorded_at=record.collected_at, valid_from=start, valid_until=end,
                                    open_ended=open_ended is True))
    return tuple(result)
