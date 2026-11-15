"""Synthetic contracts for selecting triage thresholds on declared tuning roots only (issue #74, slice 2)."""
from copy import deepcopy
import random
import json

import pytest

from agent_harness_lab.diagnostics import triage_policy as triage
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from tests.unit.diagnostics.test_triage_policy_v2 import NONE, NORMAL, SEVERE, policy, row, sha

SELECTED = 'selected_on_tuning_roots'
NAMES = (SEVERE, NORMAL, NONE)


def _draft(precision=.75, **changes):
    """A draft whose thresholds are unset: the selector is the only thing that may fill them."""
    classes = {name: dict(spec, threshold=None, min_precision=precision if name == SEVERE else .5)
               for name, spec in policy()['classes'].items()}
    lists = {name: [sha(f'{name}-c{i}') for i in range(2)] for name in NAMES}
    scope = dict(model_sha256=sha('synthetic-model'), corpus_sha256=sha('synthetic-corpus'), min_calibration_roots=3,
                 min_calibration_roots_per_class=2, calibration_class_roots_sha256=lists,
                 calibration_roots_sha256=[h for hashes in lists.values() for h in hashes])
    return policy(**{**scope, 'classes': classes, 'threshold_provenance': SELECTED, **changes})


def _tune(root, label, top, score=.9, **others):
    value = dict(row(root, label, top, score, **others), split='tuning')
    return dict(value, model_sha256=sha('synthetic-model'), corpus_sha256=sha('synthetic-corpus'))


def _base():
    return [_tune(f'{name}-t{i}', name, name) for name in NAMES for i in range(2)]


def _select(rows, minimum=2, **changes):
    return triage.select_thresholds(_draft(**changes), rows, min_roots_per_class=minimum)


def _heldout(root, label, top, score=.9):
    return dict(row(root, label, top, score), model_sha256=sha('synthetic-model'), corpus_sha256=sha('synthetic-corpus'))


def test_selects_the_lowest_threshold_that_meets_the_preregistered_targets():
    rows = _base() + [_tune('s-a', SEVERE, SEVERE, .8), _tune('s-b', SEVERE, SEVERE, .7),
                      _tune('fa-a', NONE, SEVERE, .65), _tune('fa-b', NONE, SEVERE, .5)]
    result = _select(rows)
    severe = result['classes'][SEVERE]
    # .5 gives precision 4/6 and fails; .65 gives 4/5 and is the lowest threshold that does not.
    assert result['status'] == 'selected' and result['policy']['classes'][SEVERE]['threshold'] == .65
    assert severe['tuning_precision'] == .8 and severe['tuning_recall'] == 1 and severe['tuning_roots'] == 4
    assert result['classes'][NORMAL]['threshold'] == .9 and result['classes'][NONE]['threshold'] == .9


def test_the_990_negative_10_positive_counterexample_cannot_select_a_severe_threshold():
    rows = [_tune(f'negative-{i}', NONE, NONE, .99) for i in range(990)] \
        + [_tune(f'positive-{i}', SEVERE, NONE, .99) for i in range(10)] + [_tune(f'n-{i}', NORMAL, NORMAL) for i in range(2)]
    result = _select(rows)
    assert result['status'] == 'unselected' and result['policy'] is None
    assert result['classes'][SEVERE]['threshold'] is None
    assert f'class:{SEVERE}:no_threshold_meets_preregistered_targets' in result['reasons']


def test_a_class_whose_targets_no_threshold_meets_stays_unselected_and_issues_no_policy():
    rows = _base() + [_tune('fa', NONE, SEVERE, .95)]
    result = _select(rows, precision=1)
    assert result['status'] == 'unselected' and result['policy'] is None
    assert result['reasons'] == [f'class:{SEVERE}:no_threshold_meets_preregistered_targets', *triage.SELECTION_LIMITATIONS]
    assert result['classes'][NORMAL]['threshold'] == .9


@pytest.mark.parametrize(('support', 'selected'), [pytest.param(1, False, id='below-minimum'), pytest.param(2, True, id='at-minimum')])
def test_the_per_class_tuning_minimum_is_inclusive(support, selected):
    rows = [_tune(f'{name}-t{i}', name, name) for name in (NORMAL, NONE) for i in range(2)]
    rows += [_tune(f'{SEVERE}-t{i}', SEVERE, SEVERE) for i in range(support)]
    result = _select(rows)
    assert (result['status'] == 'selected') is selected
    assert (f'class:{SEVERE}:tuning_roots_below_minimum' in result['reasons']) is (not selected)


def test_a_class_with_no_labeled_tuning_root_is_never_given_a_threshold():
    result = _select([_tune(f'{name}-t{i}', name, name) for name in (NORMAL, NONE) for i in range(2)], minimum=0)
    assert result['status'] == 'unselected' and result['policy'] is None
    assert result['classes'][SEVERE]['tuning_roots'] == 0 and result['classes'][SEVERE]['threshold'] is None


def test_repeated_rows_of_one_root_count_once():
    rows = [_tune(f'{name}-t{i}', name, name) for name in (NORMAL, NONE) for i in range(2)]
    rows += [_tune('only-severe-root', SEVERE, SEVERE)] * 20
    result = _select(rows)
    assert result['classes'][SEVERE]['tuning_roots'] == 1
    assert f'class:{SEVERE}:tuning_roots_below_minimum' in result['reasons']


def test_a_root_whose_rows_decide_different_classes_never_counts_as_a_decision():
    rows = _base() + [_tune('split-root', SEVERE, SEVERE), _tune('split-root', SEVERE, NONE)]
    severe = _select(rows)['classes'][SEVERE]
    assert severe['tuning_roots'] == 3 and severe['tuning_recall'] == 2/3


@pytest.mark.parametrize('runner_up', [pytest.param(.85, id='margin-below-minimum'), pytest.param(.9, id='tied-top-classes')])
def test_rows_that_would_abstain_never_support_a_threshold(runner_up):
    rows = _base() + [_tune('abstaining', SEVERE, SEVERE, .9, **{NORMAL: runner_up})]
    severe = _select(rows)['classes'][SEVERE]
    assert severe['tuning_roots'] == 3 and severe['tuning_recall'] == 2/3 and severe['threshold'] == .9


def test_unlabeled_tuning_roots_are_declared_but_never_become_labels():
    labeled = _select(_base())
    unlabeled = _select(_base() + [_tune('unlabeled-alert', None, SEVERE, .99)])
    assert unlabeled['classes'] == labeled['classes'] and unlabeled['unlabeled_roots'] == 1
    assert triage.root_sha256('unlabeled-alert') in unlabeled['policy']['threshold_tuning_roots_sha256']


def test_selection_declares_every_tuning_root_in_the_frozen_policy():
    result = _select(_base())
    declared = result['policy']['threshold_tuning_roots_sha256']
    assert declared == sorted(triage.root_sha256(r['root_case_id']) for r in _base()) and result['tuning_roots_sha256'] == declared
    assert result['policy']['threshold_provenance'] == SELECTED


def test_selection_refuses_heldout_rows():
    with pytest.raises(ValueError, match='tuning rows'):
        _select(_base() + [_heldout('validation-root', NONE, NONE)])


@pytest.mark.parametrize('role', ['fit_roots_sha256', 'calibration_roots_sha256'])
def test_selection_refuses_roots_declared_for_another_role(role):
    held = {**_draft(), role: [*_draft()[role], triage.root_sha256('reused-root')]}
    with pytest.raises(ValueError, match=role):
        triage.select_thresholds(held, _base() + [_tune('reused-root', NONE, NONE)], min_roots_per_class=2)


@pytest.mark.parametrize('field', ['model_sha256', 'corpus_sha256'])
def test_selection_refuses_rows_from_another_or_unstated_revision(field):
    other = _base()
    other[0] = dict(other[0], **{field: sha('synthetic-other')})
    with pytest.raises(ValueError, match='outside the policy scope'):
        _select(other)
    del other[0][field]
    with pytest.raises(ValueError, match='outside the policy scope'):
        _select(other)


@pytest.mark.parametrize('changes', [
    pytest.param(dict(threshold_provenance='human_preregistered'), id='preregistered-provenance'),
    pytest.param(dict(threshold_tuning_roots_sha256=[sha('declared-early')]), id='tuning-roots-already-declared'),
    pytest.param(dict(model_sha256=None), id='no-model-revision'),
    pytest.param(dict(min_calibration_roots_per_class=-1), id='invalid-class-calibration'),
])
def test_selection_refuses_a_draft_that_is_not_ready_to_select(changes):
    with pytest.raises(ValueError):
        _select(_base(), **changes)


def test_selection_refuses_a_draft_that_already_carries_a_threshold():
    draft = _draft()
    draft['classes'][SEVERE]['threshold'] = .5
    with pytest.raises(ValueError, match='unset'):
        triage.select_thresholds(draft, _base(), min_roots_per_class=2)


@pytest.mark.parametrize('minimum', [-1, True, 1.5, None])
def test_selection_requires_an_explicit_nonnegative_integer_minimum(minimum):
    with pytest.raises(ValueError, match='minimum'):
        triage.select_thresholds(_draft(), _base(), min_roots_per_class=minimum)


@pytest.mark.parametrize('bad', [
    pytest.param([], id='empty'),
    pytest.param([dict(_tune('a', NONE, NONE), label='unknown_class')], id='label-outside-taxonomy'),
    pytest.param([dict(_tune('a', NONE, NONE), scores={SEVERE: float('nan'), NORMAL: 0., NONE: .9})], id='nan-score'),
    pytest.param([dict(_tune('a', NONE, NONE), scores={SEVERE: 0., NONE: .9})], id='missing-class-score'),
    pytest.param([_tune('a', NONE, NONE), _tune('a', SEVERE, NONE)], id='conflicting-labels'),
])
def test_selection_refuses_invalid_tuning_input(bad):
    with pytest.raises(ValueError):
        _select(bad)


def test_selection_is_private_non_certifying_and_json_safe():
    result = _select(_base())
    json.dumps(result, allow_nan=False)
    assert result['certification'] == 'not_issued' and result['publication_allowed'] is False
    assert result['automatic_promotion'] is False and result['threshold_authority'] == 'selected_on_tuning_roots_unverified'
    assert not public_export_allowed([result]) and set(triage.SELECTION_LIMITATIONS) <= set(result['reasons'])
    assert all(c['authority'] == 'same_sample_descriptive_only' for c in result['classes'].values())


def test_selection_does_not_mutate_its_inputs_and_is_deterministic():
    draft, rows = _draft(), _base() + [_tune('s-a', SEVERE, SEVERE, .8)]
    before = deepcopy((draft, rows))
    first = triage.select_thresholds(draft, iter(rows), min_roots_per_class=2)
    assert (draft, rows) == before
    assert first == triage.select_thresholds(draft, reversed(rows), min_roots_per_class=2)


def _selected(rows=None):
    return _select(rows or _base())['policy']


def test_v9_evaluates_a_selected_policy_without_granting_authority():
    report = triage.evaluate(_selected(), [_heldout('validation-root', NONE, NONE)], report_version=9)
    assert report['profile'] == 'diagnostic-triage-evaluation-v9' and report['status'] != 'accepted_for_shadow'
    assert report['threshold_authority'] == 'selected_on_declared_tuning_roots_unverified'
    assert report['threshold_selection'] == 'refused_in_validation' and report['certification'] == 'not_issued'
    assert not report['automatic_promotion'] and not report['publication_allowed'] and not public_export_allowed([report])


def test_v9_keeps_v8_decisions_diagnostics_and_authority_for_a_preregistered_policy():
    frozen = _selected()
    frozen = dict(frozen, threshold_provenance='human_preregistered', threshold_tuning_roots_sha256=[])
    rows = [_heldout('a', NONE, NONE), _heldout('b', SEVERE, NONE), _heldout('c', None, None)]
    v8, v9 = triage.evaluate(frozen, rows, report_version=8), triage.evaluate(frozen, rows, report_version=9)
    ignored = ('profile', 'schema_version')
    assert {k: v for k, v in v9.items() if k not in ignored} == {k: v for k, v in v8.items() if k not in ignored}
    assert v9['threshold_authority'] == 'human_preregistered_unverified'


def test_v9_refuses_a_selected_policy_that_declares_no_tuning_roots():
    with pytest.raises(ValueError, match='tuning roots'):
        triage.evaluate(_draft(threshold_tuning_roots_sha256=[], classes=_selected()['classes']), [_heldout('a', NONE, NONE)], report_version=9)


def test_v9_refuses_a_validation_root_that_was_used_for_tuning():
    with pytest.raises(ValueError, match='threshold_tuning_roots_sha256'):
        triage.evaluate(_selected(), [_heldout(f'{NONE}-t0', NONE, NONE)], report_version=9)


@pytest.mark.parametrize('version', [2, 3, 4, 5, 6, 7, 8])
def test_earlier_profiles_still_refuse_selected_thresholds(version):
    with pytest.raises(ValueError, match='invalid triage policy v2 schema|human-preregistered'):
        triage.evaluate(_selected(), [_heldout('a', NONE, NONE)], report_version=version)


def test_the_default_policy_check_still_refuses_selected_thresholds():
    with pytest.raises(ValueError, match='human-preregistered'):
        triage.check_policy(_selected(), require_scope=True, require_class_calibration=True)


def test_validation_labels_change_the_report_but_never_the_selected_artifacts():
    rows = _base()
    selected = _select(rows)['policy']
    before = deepcopy(selected)
    agreeing = [_heldout('a', NONE, NONE), _heldout('b', SEVERE, SEVERE)]
    relabeled = [_heldout('a', SEVERE, NONE), _heldout('b', NONE, SEVERE)]
    first, second = (triage.evaluate(selected, r, report_version=9) for r in (agreeing, relabeled))
    assert selected == before and first['classes'] != second['classes']
    for key in ('policy_sha256', 'model_sha256', 'calibrator_sha256', 'decisions', 'threshold_authority'):
        assert first[key] == second[key]
    assert _select(rows)['policy'] == before


def test_a_tuning_change_moves_the_thresholds_but_never_the_heldout_labels():
    heldout = [_heldout('a', NONE, NONE, .75)]
    labels = [r['label'] for r in heldout]
    low = _select(_base() + [_tune('x', NONE, NONE, .7)])
    high = _select(_base())
    assert low['policy']['classes'][NONE]['threshold'] == .7 and high['policy']['classes'][NONE]['threshold'] == .9
    assert [r['label'] for r in heldout] == labels
    assert triage.evaluate(low['policy'], heldout, report_version=9)['decisions'][0]['value'] == NONE
    assert triage.evaluate(high['policy'], heldout, report_version=9)['decisions'][0]['value'] is None


def _seeded_tuning_rows():
    """Synthetic roots with one to three rows each; the true class usually leads, and right rows tend to score higher."""
    rng = random.Random(74)
    rows = []
    for index in range(90):
        label = NAMES[index % 3]
        for repeat in range(rng.randint(1, 3)):
            top = label if rng.random() < .75 else rng.choice(NAMES)
            score = rng.uniform(.6, 1) if top == label else rng.uniform(.3, .7)
            rows.append(_tune(f'seeded-{index}', label, top, round(score, 2), **{
                name: round(rng.uniform(0, .25), 2) for name in NAMES if name != top}))
    return rows


def _floors(rows, name):
    """Each root's lowest top score when every row decides `name` with a unique leader and the .1 margin; else the root is skipped."""
    by_root = {}
    for r in rows:
        ranked = sorted(r['scores'].values(), reverse=True)
        decides = max(r['scores'], key=r['scores'].get) == name and ranked[0] != ranked[1] and ranked[0] - ranked[1] >= .1
        by_root.setdefault(r['root_case_id'], []).append(r['scores'][name] if decides else -1)
    return {min(v) for v in by_root.values() if min(v) >= 0}


def test_selected_thresholds_agree_with_the_evaluator_and_no_lower_threshold_qualifies():
    rows = _seeded_tuning_rows()
    result = _select(rows, minimum=5, precision=.9)
    assert result['status'] == 'selected'
    frozen = dict(result['policy'], threshold_provenance='human_preregistered', threshold_tuning_roots_sha256=[])
    replay = [dict(r, split='heldout') for r in rows]
    report = triage.evaluate(frozen, replay, report_version=8)
    for name in NAMES:
        chosen = result['classes'][name]
        assert report['classes'][name]['precision'] == chosen['tuning_precision']
        assert report['classes'][name]['recall'] == chosen['tuning_recall']
        target = frozen['classes'][name]
        assert chosen['tuning_precision'] >= target['min_precision'] and chosen['tuning_recall'] >= target['min_recall']
        for value in (v for v in _floors(rows, name) if v < chosen['threshold']):
            probe = deepcopy(frozen)
            probe['classes'][name]['threshold'] = value
            metrics = triage.evaluate(probe, replay, report_version=8)['classes'][name]
            assert not (metrics['precision'] is not None and metrics['precision'] >= target['min_precision']
                        and metrics['recall'] >= target['min_recall']), (name, value)
