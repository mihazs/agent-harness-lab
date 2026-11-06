"""Mechanism coverage: observed patterns, supported diagnoses and validated repairs stay distinct.

Coverage is bookkeeping over reviewed ``MechanismRecord`` dispositions, never a
causal claim. A record without captured evidence is ``unknown_capture`` whatever
disposition it carries, and a run whose capture completeness is not declared
``complete`` cannot report complete coverage: unseen mechanisms may exist.
"""
import hashlib
import json

from agent_harness_lab.diagnostics.mechanisms import COMPONENTS, DISPOSITIONS, SEVERITIES, MechanismRecord
from agent_harness_lab.evaluation.history_report import _cell

COVERAGE_PROFILE = 'mechanism-coverage-v1'
CAPTURE_STATES = ('complete', 'partial', 'unknown')
CLAIM_LEVELS = ('none', 'observed_pattern', 'supported_diagnosis', 'development_recommendation',
                'validated_improvement')
COVERAGE_STATES = ('resolved', 'awaiting_certification', 'open', 'unknown_capture')
_RESOLVED = frozenset({'validated', 'rejected', 'already_covered', 'outside_scope'})
_CLAIMED = {'validated': 'validated_improvement', 'recommended': 'development_recommendation'}


def _record(value):
    if isinstance(value, MechanismRecord):
        return value.validate()
    if not isinstance(value, dict) or value.get('schema_version') != 1:
        raise ValueError('mechanism record or schema_version 1 mapping required')
    fields = {k: v for k, v in value.items() if k != 'schema_version'}
    try:
        return MechanismRecord(**fields).validate()
    except TypeError as error:
        raise ValueError('invalid mechanism record fields') from error


def _component(mechanism):
    """The responsible component the record supports; unsupported routes abstain."""
    if not mechanism.evidence:
        return 'insufficient_evidence'
    if mechanism.component == 'no_change':
        return 'no_change'
    return mechanism.component if mechanism.actionable else 'insufficient_evidence'


def _claim(mechanism, component):
    if not mechanism.evidence:
        return 'none'
    if component in ('insufficient_evidence', 'no_change'):
        return 'observed_pattern'
    return _CLAIMED.get(mechanism.disposition, 'supported_diagnosis')


def _coverage(mechanism):
    if not mechanism.evidence:
        return 'unknown_capture'
    if mechanism.disposition in _RESOLVED:
        return 'resolved'
    return 'awaiting_certification' if mechanism.disposition == 'recommended' else 'open'


def _entry(mechanism):
    component = _component(mechanism)
    claim = _claim(mechanism, component)
    limitations = []
    if mechanism.disposition in _CLAIMED and claim != _CLAIMED[mechanism.disposition]:
        limitations.append('disposition_exceeds_supported_claim')
    if not mechanism.evidence and mechanism.disposition in _RESOLVED | {'recommended'}:
        limitations.append('disposition_without_captured_evidence')
    if mechanism.exposure is None:
        limitations.append('exposure_denominator_unknown')
    return dict(mechanism_id=mechanism.mechanism_id, key=mechanism.key, severity=mechanism.severity,
                evidence_version=mechanism.evidence_version, component_version=mechanism.component_version,
                capture='captured' if mechanism.evidence else 'unknown', evidence_count=len(mechanism.evidence),
                exposure=mechanism.exposure, recorded_component=mechanism.component,
                responsible_component=component, claim_level=claim, disposition=mechanism.disposition,
                reason=mechanism.reason, limitations=limitations,
                # A disposition the record cannot support needs review; it is not a coverage pass.
                coverage='open' if mechanism.evidence and 'disposition_exceeds_supported_claim' in limitations
                else _coverage(mechanism))


def _counts(entries, name, vocabulary):
    return {value: sum(entry[name] == value for entry in entries) for value in vocabulary}


def report(mechanisms, *, capture='unknown'):
    """Return the private ``mechanism-coverage-v1`` JSON report for ``mechanisms``.

    ``mechanisms`` are records or their ``as_dict`` mappings (as ``run_queue``
    returns). Identical keys are suppressed and listed; one mechanism ID under
    several evidence/component versions stays separate entries, listed as
    ``multiple_versions``. ``capture`` is the controller's declaration for the
    run. Pure: nothing is evaluated, charged or mutated.
    """
    if capture not in CAPTURE_STATES:
        raise ValueError('capture must be complete, partial or unknown')
    entries, duplicates, keys, versions = [], [], set(), {}
    for value in mechanisms:
        mechanism = _record(value)
        if mechanism.key in keys:
            duplicates.append(dict(mechanism_id=mechanism.mechanism_id, key=mechanism.key))
            continue
        keys.add(mechanism.key)
        versions.setdefault(mechanism.mechanism_id, []).append(mechanism.key)
        entries.append(_entry(mechanism))
    entries.sort(key=lambda e: (SEVERITIES.index(e['severity']), e['mechanism_id'], e['key']))
    limitations = ['taxonomy_and_routes_are_hypotheses_not_causal_proof']
    if not entries:
        limitations.append('no_mechanisms_recorded')
    if capture != 'complete':
        limitations.append('capture_' + capture + '_unseen_mechanisms_possible')
    totals = dict(mechanisms=len(entries), duplicates_suppressed=len(duplicates),
                  coverage=_counts(entries, 'coverage', COVERAGE_STATES),
                  claim_level=_counts(entries, 'claim_level', CLAIM_LEVELS),
                  disposition=_counts(entries, 'disposition', sorted(DISPOSITIONS)),
                  responsible_component=_counts(entries, 'responsible_component', sorted(COMPONENTS)),
                  exposure_unknown=sum(entry['exposure'] is None for entry in entries))
    body = dict(schema_version=1, profile=COVERAGE_PROFILE, publication_allowed=False, authority='none',
                diagnosis_established=False, causal_claim=False, capture=capture, entries=entries,
                duplicates=duplicates,
                multiple_versions={name: keys for name, keys in sorted(versions.items()) if len(keys) > 1},
                totals=totals, limitations=limitations,
                coverage_complete=bool(entries) and capture == 'complete'
                and totals['coverage']['resolved'] == len(entries))
    body['sha256'] = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    return body


def render_markdown(result):
    """Human report for a coverage report; every recorded string is escaped untrusted text."""
    if result.get('profile') != COVERAGE_PROFILE or result.get('publication_allowed') is not False:
        raise ValueError('private mechanism coverage report required')
    totals = result['totals']
    lines = ['# Private mechanism coverage report', '',
             'Routes and claim levels are hypotheses, not causal proof; development recommendations '
             'are not certified repairs.', '',
             f"Capture: {_cell(result['capture'])}. Coverage complete: "
             f"{'yes' if result['coverage_complete'] else 'no'}. Mechanisms: {totals['mechanisms']} "
             f"({totals['duplicates_suppressed']} duplicates suppressed, "
             f"{totals['exposure_unknown']} with unknown exposure).", '',
             '| Coverage | Count |', '| --- | --- |']
    lines += [f'| {name} | {count} |' for name, count in totals['coverage'].items()]
    lines += ['', '| Mechanism | Severity | Responsible component | Claim | Disposition | Coverage | Reason |',
              '| --- | --- | --- | --- | --- | --- | --- |']
    lines += ['| ' + ' | '.join(_cell(entry[name]) for name in
                                ('mechanism_id', 'severity', 'responsible_component', 'claim_level',
                                 'disposition', 'coverage', 'reason')) + ' |' for entry in result['entries']]
    lines += ['', 'Limitations: ' + ', '.join(_cell(item) for item in result['limitations']), '']
    return '\n'.join(lines)
