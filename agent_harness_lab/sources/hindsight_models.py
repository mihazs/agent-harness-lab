"""Selected Hindsight synthesized model metadata; never executes reflect."""
from dataclasses import dataclass
from .hardened import limits
from .hindsight import LIMITS, iso
from .hindsight_knowledge import HindsightBankPart, _identity, _time
from .schema import digest

PROVIDER = 'hindsight-mental-models'
SOURCE_VERSION = 'v1'


def _text(value):
    if value is not None and not isinstance(value, str): raise ValueError('invalid Hindsight model text')
    return digest(value) if value is not None else None


@dataclass(frozen=True)
class MentalModel:
    claim_type: str
    native_id_sha256: str
    name_sha256: str | None
    query_sha256: str | None
    content_sha256: str | None
    refreshed_at: str | None
    refreshed_at_status: str
    original_at: None = None


class HindsightModels(HindsightBankPart):
    """Read bounded synthesized model rows from one selected extracted bank archive."""

    def read(self, source):
        selection, rows, hashes = self._bank_part(source, PROVIDER, 'mental_models.json', 'mental_model_count', False)
        instance, bank = selection['instance'], selection['bank_id']
        max_fields = limits(selection.get('limits'), LIMITS)['max_fields']
        claims, seen = [], set()
        for row in rows:
            if not isinstance(row, dict) or len(row) > max_fields or row.get('bank_id') != bank:
                raise ValueError('invalid Hindsight mental model scope')
            native = _identity(row.get('id'))
            if native in seen: raise ValueError('duplicate Hindsight mental model')
            seen.add(native)
            refreshed, status = _time(row.get('last_refreshed_at'))
            claims.append(MentalModel('mental_model', digest(('hindsight.knowledge', instance, bank, 'model', native)),
                _text(row.get('name')), _text(row.get('source_query')), _text(row.get('content')), iso(refreshed), status))
        warnings = ['archive_origin_unverified', 'model_output_is_synthesis', 'source_relationships_unverified',
                    'lineage_unknown', 'completeness_unknown']
        return self._snapshot(source, selection, claims, hashes, PROVIDER, warnings, 'model_hypothesis', 'hindsight.models_archive')
