"""Unverified references reported by selected Hindsight model rows; no verified edges."""
from dataclasses import dataclass
from .hardened import HostileReport, limits, load_json
from .hindsight import LIMITS
from .hindsight_knowledge import HindsightBankPart, _identity
from .schema import digest

PROVIDER = 'hindsight-model-references'
SOURCE_VERSION = 'v1'
CATEGORIES = ('memories', 'mental_models', 'directives')


@dataclass(frozen=True)
class ReferenceCoverage:
    claim_type: str
    native_id_sha256: str
    model_sha256: str
    memories: str
    mental_models: str
    directives: str
    original_at: None = None


@dataclass(frozen=True)
class ModelReference:
    claim_type: str
    native_id_sha256: str
    model_sha256: str
    target_sha256: str | None
    target_identity: str
    category: str
    position: int
    original_at: None = None


class HindsightModelReferences(HindsightBankPart):
    """Read one bank's reported model references without asserting source relationships."""

    def read(self, source):
        version = source.version
        if version not in ('v1', 'v2'): raise ValueError('unsupported Hindsight reference version')
        result = self._bank_part(source, PROVIDER, 'mental_models.json', 'mental_model_count', False,
            version=version, metadata=version == 'v2')
        selection, rows, hashes = result[:3]
        manifest, remaining = result[3:] if version == 'v2' else (None, None)
        instance, bank = selection['instance'], selection['bank_id']
        bounds = limits(selection.get('limits'), LIMITS)
        claims, seen, references = [], set(), 0
        for row in rows:
            if not isinstance(row, dict) or len(row) > bounds['max_fields'] or row.get('bank_id') != bank:
                raise ValueError('invalid Hindsight model reference scope')
            native = _identity(row.get('id'))
            if native in seen: raise ValueError('duplicate Hindsight model')
            seen.add(native)
            model = digest(('hindsight.knowledge', instance, bank, 'model', native))
            response = row.get('reflect_response')
            if (version == 'v2' and manifest.get('bank_rows_json_encoding') == 'serialized'
                    and isinstance(response, str)):
                spent = []
                try:
                    response = load_json(response.encode('utf-8'), dict(bounds, **remaining), 'Hindsight model reference', spent=spent)
                except HostileReport:
                    raise
                except (ValueError, UnicodeEncodeError):
                    response = None
                finally:
                    if spent:
                        remaining['max_json_containers'] -= spent[0][0]
                        remaining['max_json_values'] -= spent[0][1]
            elif version == 'v2' and manifest.get('bank_rows_json_encoding') == 'serialized':
                response = None
            based = response.get('based_on') if isinstance(response, dict) else None
            states = {}
            for category in CATEGORIES:
                values = based.get(category) if isinstance(based, dict) else None
                states[category] = 'reported_list' if isinstance(values, list) else 'unknown'
                if not isinstance(values, list): continue
                references += len(values)
                if len(rows)+references > bounds['max_rows']: raise HostileReport('Hindsight model reference bound exceeded')
                for position, value in enumerate(values):
                    target = value.get('id') if isinstance(value, dict) else None
                    status = 'missing' if isinstance(value, dict) and target is None else 'invalid'
                    if target is not None:
                        try: target = _identity(target); status = 'reported'
                        except ValueError: target = None
                    target_hash = digest(('hindsight.model_ref_target', instance, bank, category, target)) if target is not None else None
                    native_ref = digest(('hindsight.model_ref', instance, bank, native, category, position))
                    claims.append(ModelReference('model_reference', native_ref, model, target_hash, status, category, position))
            coverage = digest(('hindsight.model_ref_coverage', instance, bank, native))
            claims.append(ReferenceCoverage('model_reference_coverage', coverage, model, **states))
        warnings = ['archive_origin_unverified', 'reported_references_not_verified_edges', 'model_output_is_synthesis',
                    'encoded_or_missing_fields_unknown', 'lineage_unknown', 'completeness_unknown']
        if version == 'v2': warnings.append('row_encoding_declaration_unverified')
        return self._snapshot(source, selection, claims, hashes, PROVIDER, warnings, 'model_hypothesis',
            'hindsight.model_refs_archive', version=version)
