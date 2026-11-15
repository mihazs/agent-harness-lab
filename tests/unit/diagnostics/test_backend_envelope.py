"""Synthetic contracts for the versioned diagnostic backend prediction envelope (#14)."""
import copy
import json
from unittest.mock import patch

import pytest

from agent_harness_lab.diagnostics import backend_envelope

SEVERE, OTHER = 'tool_misuse', 'instruction_drift'
ROOTS = ['root-a', 'root-b', 'root-c']
HASH = 'a' * 64


def frozen(**changes):
    value = dict(schema_version=1, profile='diagnostic-backend-frozen-v1', taxonomy_revision='tax-1',
                 classes=[SEVERE, OTHER], roots=list(ROOTS))
    value.update(changes)
    return value


def envelope(backend='lexical', root='root-a', status='predicted', label=SEVERE, **changes):
    value = dict(
        schema_version=1, profile='diagnostic-prediction-envelope-v1', root_case_id=root, split='heldout',
        backend=dict(kind='tfidf', ref=backend, revision='rev-1', preprocessing_sha256=HASH,
                     taxonomy_revision='tax-1', score_semantics='rank_score', calibration_ref=None),
        fit=dict(roles=['train'], demonstration_roots=[]),
        input=dict(fields=['summary', 'tool_events'], max_chars=2000, truncated=False),
        outcome=dict(status=status, label=label if status == 'predicted' else None,
                     score=0.5 if status == 'predicted' else None,
                     reason=None if status == 'predicted' else 'low_margin'),
        resources=dict(phase='warm', latency_seconds=0.25, peak_memory_bytes=1000, cost=0))
    for key, item in changes.items():
        value[key] = item
    return value


def complete(*backends):
    return [envelope(backend, root) for backend in backends for root in ROOTS]


def tweak(record, section, **fields):
    record = copy.deepcopy(record)
    record[section].update(fields)
    return record


def test_complete_backends_are_described_without_a_winner_or_authority():
    result = backend_envelope.evaluate(frozen(), complete('lexical', 'rules'))
    assert result['status'] == 'insufficient_evidence'
    assert result['roots'] == 3 and result['comparable_roots'] == 3
    assert {name: row['coverage'] for name, row in result['backends'].items()} == dict(lexical=1.0, rules=1.0)
    assert result['quality'] == 'not_computed' and result['selection'] == 'not_issued'
    assert result['promotion_authority'] is False and result['publication_allowed'] is False
    assert 'label_authority_unverified' in result['reasons']
    assert 'winner' not in result and 'best_backend' not in result


def test_roots_a_backend_did_not_answer_stay_visible_as_missing():
    records = complete('lexical', 'rules')[:-1]
    result = backend_envelope.evaluate(frozen(), records)
    rules = result['backends']['rules']
    assert (rules['records'], rules['missing_roots'], rules['coverage']) == (2, 1, 2 / 3)
    assert result['comparable_roots'] == 2
    assert 'incomplete_backend_results' in result['reasons']


def test_always_abstaining_backend_reports_zero_coverage():
    records = [envelope('silent', root, status='abstained') for root in ROOTS]
    row = backend_envelope.evaluate(frozen(), records)['backends']['silent']
    assert (row['abstained'], row['predicted'], row['coverage']) == (3, 0, 0.0)
    assert 'zero_coverage_backend' in backend_envelope.evaluate(frozen(), records)['reasons']


def test_failed_runs_are_counted_apart_from_abstentions():
    records = [envelope('local', 'root-a'), envelope('local', 'root-b', status='failed'),
               envelope('local', 'root-c', status='abstained')]
    row = backend_envelope.evaluate(frozen(), records)['backends']['local']
    assert (row['predicted'], row['failed'], row['abstained']) == (1, 1, 1)
    assert row['coverage'] == 1 / 3


def test_backend_whose_preprocessing_changed_midway_is_rejected():
    records = complete('lexical')
    records[1] = tweak(records[1], 'backend', preprocessing_sha256='b' * 64)
    result = backend_envelope.evaluate(frozen(), records)
    assert result['status'] == 'rejected'
    assert 'backend_identity_changed' in result['reasons']
    assert result['backends']['lexical']['identity_sha256'] is None


def test_fit_that_used_heldout_roles_is_rejected():
    records = [tweak(r, 'fit', roles=['train', 'heldout']) for r in complete('lexical')]
    result = backend_envelope.evaluate(frozen(), records)
    assert result['status'] == 'rejected'
    assert 'heldout_role_used_in_fit' in result['reasons']


def test_demonstration_drawn_from_an_evaluated_root_is_rejected():
    records = [tweak(r, 'fit', demonstration_roots=['root-b']) for r in complete('local')]
    result = backend_envelope.evaluate(frozen(), records)
    assert result['status'] == 'rejected'
    assert 'demonstration_root_is_evaluated' in result['reasons']


def test_demonstration_from_an_unrelated_root_is_accepted():
    records = [tweak(r, 'fit', demonstration_roots=['root-train']) for r in complete('local')]
    assert backend_envelope.evaluate(frozen(), records)['status'] == 'insufficient_evidence'


@pytest.mark.parametrize('field', ['label', 'Reward', 'ground_truth', ' outcome '],
                         ids=['label', 'reward-cased', 'ground-truth', 'outcome-padded'])
def test_label_or_reward_among_the_model_inputs_is_rejected(field):
    records = [tweak(r, 'input', fields=['summary', field]) for r in complete('lexical')]
    result = backend_envelope.evaluate(frozen(), records)
    assert result['status'] == 'rejected'
    assert 'label_or_reward_in_model_input' in result['reasons']


@pytest.mark.parametrize('kind, calibration_ref', [
    ('nli', 'cal-1'),
    ('recorded_response', 'cal-1'),
    ('tfidf', None),
], ids=['nli-entailment-is-not-a-probability', 'self-reported-confidence-is-not-a-probability',
        'probability-without-calibration-ref'])
def test_score_claimed_as_probability_without_support_is_rejected(kind, calibration_ref):
    records = [tweak(r, 'backend', kind=kind, score_semantics='calibrated_probability', calibration_ref=calibration_ref)
               for r in complete('local')]
    result = backend_envelope.evaluate(frozen(), records)
    assert result['status'] == 'rejected'
    assert 'unsupported_probability_claim' in result['reasons']


def test_similarity_score_declared_as_similarity_is_not_a_probability_claim():
    records = [tweak(r, 'backend', kind='embeddings', score_semantics='similarity') for r in complete('local')]
    assert backend_envelope.evaluate(frozen(), records)['status'] == 'insufficient_evidence'


def test_declared_calibration_is_recorded_but_never_verified():
    records = [tweak(r, 'backend', score_semantics='calibrated_probability', calibration_ref='cal-1')
               for r in complete('local')]
    result = backend_envelope.evaluate(frozen(), records)
    assert result['status'] == 'insufficient_evidence'
    assert 'calibration_unverified' in result['reasons']


def test_unmeasured_resources_stay_unknown_and_cold_is_not_mixed_with_warm():
    cold = tweak(envelope('local', 'root-a'), 'resources', phase='cold', latency_seconds=9.0)
    unmeasured = tweak(envelope('local', 'root-b'), 'resources', latency_seconds=None, peak_memory_bytes=None)
    warm = envelope('local', 'root-c')
    resources = backend_envelope.evaluate(frozen(), [cold, unmeasured, warm])['backends']['local']['resources']
    assert resources['cold']['latency_seconds'] == 9.0
    assert resources['warm']['latency_seconds'] is None and resources['warm']['peak_memory_bytes'] is None
    assert resources['warm']['unmeasured'] == ['latency_seconds', 'peak_memory_bytes']
    assert resources['warm']['records'] == 2


def test_truncated_and_unknown_truncation_are_counted_separately():
    records = [tweak(envelope('local', 'root-a'), 'input', truncated=True),
               tweak(envelope('local', 'root-b'), 'input', truncated=None), envelope('local', 'root-c')]
    result = backend_envelope.evaluate(frozen(), records)
    assert result['backends']['local']['truncated'] == dict(yes=1, unknown=1, no=1)
    assert 'truncation_unknown' in result['reasons']


def test_backends_with_different_evidence_windows_are_not_a_same_input_comparison():
    records = complete('lexical') + [tweak(r, 'input', max_chars=500) for r in complete('rules')]
    assert 'evidence_windows_differ' in backend_envelope.evaluate(frozen(), records)['reasons']


def test_report_carries_counts_only_never_roots_or_inputs():
    roots = ['root-secret-canary-1', 'root-secret-canary-2']
    records = [envelope('lexical', root) for root in roots]
    result = backend_envelope.evaluate(frozen(roots=roots), records)
    assert 'secret-canary' not in json.dumps(result)


def test_evaluation_runs_offline_without_a_process():
    with patch('socket.socket', side_effect=AssertionError('unexpected network')), \
            patch('subprocess.Popen', side_effect=AssertionError('unexpected process')):
        assert backend_envelope.evaluate(frozen(), complete('lexical'))['status'] == 'insufficient_evidence'


def _drop(section, key):
    def mutate(record):
        del record[section][key]
    return mutate


def _set(path, value):
    def mutate(record):
        target = record
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
    return mutate


MALFORMED = {
    'outcome-omitted': lambda r: r.pop('outcome'),
    'status-unknown': _set(('outcome', 'status'), 'maybe'),
    'predicted-without-label': _set(('outcome', 'label'), None),
    'label-outside-taxonomy': _set(('outcome', 'label'), 'invented_class'),
    'abstained-with-label': lambda r: r['outcome'].update(status='abstained'),
    'failed-without-reason': lambda r: r['outcome'].update(status='failed', label=None, reason=None),
    'not-heldout': _set(('split',), 'train'),
    'root-outside-frozen-roots': _set(('root_case_id',), 'root-unlisted'),
    'taxonomy-revision-differs': _set(('backend', 'taxonomy_revision'), 'tax-2'),
    'unknown-kind': _set(('backend', 'kind'), 'oracle'),
    'unknown-score-semantics': _set(('backend', 'score_semantics'), 'truth'),
    'preprocessing-not-a-hash': _set(('backend', 'preprocessing_sha256'), 'abc'),
    'unknown-fit-role': _set(('fit', 'roles'), ['train', 'secret']),
    'fit-roles-omitted': _drop('fit', 'roles'),
    'input-fields-empty': _set(('input', 'fields'), []),
    'window-zero': _set(('input', 'max_chars'), 0),
    'truncated-not-boolean': _set(('input', 'truncated'), 'no'),
    'resource-key-omitted': _drop('resources', 'cost'),
    'phase-unknown': _set(('resources', 'phase'), 'hot'),
    'latency-negative': _set(('resources', 'latency_seconds'), -1),
    'latency-boolean': _set(('resources', 'latency_seconds'), True),
    'latency-nan': _set(('resources', 'latency_seconds'), float('nan')),
    'score-nan': _set(('outcome', 'score'), float('nan')),
    'schema-version-two': _set(('schema_version',), 2),
}


@pytest.mark.parametrize('name', MALFORMED)
def test_malformed_envelope_is_refused_rather_than_counted(name):
    record = envelope('lexical')
    MALFORMED[name](record)
    with pytest.raises(ValueError):
        backend_envelope.evaluate(frozen(), [record, envelope('lexical', 'root-b'), envelope('lexical', 'root-c')])


@pytest.mark.parametrize('records', [
    [],
    [envelope('lexical'), envelope('lexical')],
], ids=['empty', 'repeated-backend-and-root'])
def test_empty_or_repeated_input_is_refused(records):
    with pytest.raises(ValueError):
        backend_envelope.evaluate(frozen(), records)


@pytest.mark.parametrize('changes', [
    dict(roots=[]),
    dict(roots=['root-a', 'root-a']),
    dict(classes=[SEVERE]),
    dict(profile='other'),
    dict(taxonomy_revision=''),
], ids=['no-roots', 'duplicate-roots', 'one-class', 'wrong-profile', 'blank-revision'])
def test_invalid_frozen_declaration_is_refused(changes):
    with pytest.raises(ValueError):
        backend_envelope.evaluate(frozen(**changes), complete('lexical'))
