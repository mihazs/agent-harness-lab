"""Paired skill-present versus skill-absent transfer under a predeclared policy; synthetic records only."""
import pytest

from agent_harness_lab.evaluation.skills import TRANSFER_SCHEMA, paired_transfer

POLICY = dict(target_conditions=['natural'], control_conditions=['negative_routing'], max_control_loss=0.0)


def trial(condition, arm, task, outcome, *, root=None, provenance='executable_grader', **extra):
    return dict(condition=condition, arm=arm, task_id=task, root_id='root-' + task if root is None else root, outcome=outcome,
                outcome_provenance=provenance, **extra)


def pairs(condition, absent, present, prefix):
    """One absent and one present trial per task root, with the given outcome sequences."""
    rows = []
    for index, (before, after) in enumerate(zip(absent, present)):
        task = f'{prefix}{index}'
        rows += [trial(condition, 'absent', task, before), trial(condition, 'present', task, after)]
    return rows


def helpful_but_harmful():
    return (pairs('natural', [False] * 4, [True] * 4, 'target')
            + pairs('negative_routing', [True] * 4, [True, False, True, False], 'control'))


def test_a_skill_that_helps_its_target_but_harms_an_unrelated_control_is_rejected():
    report = paired_transfer(helpful_but_harmful(), policy=POLICY)
    assert (report['disposition'], report['reason']) == ('rejected', 'negative_transfer')
    target, control = report['conditions']['natural'], report['conditions']['negative_routing']
    assert (target['role'], target['matched_roots'], target['improved_roots'], target['mean_delta']) == ('target', 4, 4, 1.0)
    assert (control['role'], control['matched_roots'], control['harmed_roots'], control['mean_delta']) == ('control', 4, 2, -0.5)
    assert report['schema'] == TRANSFER_SCHEMA
    assert report['automatic_promotion'] is False
    assert report['causal_effect'] == 'requires_frozen_paired_experiment'


def test_control_loss_within_the_declared_tolerance_stays_uncertified():
    rows = pairs('natural', [False] * 4, [True] * 4, 't') + pairs('negative_routing', [True] * 10, [True] * 9 + [False], 'c')
    tolerant = dict(POLICY, max_control_loss=0.1)
    report = paired_transfer(rows, policy=tolerant)
    assert (report['disposition'], report['reason']) == ('uncertified', 'requires_frozen_paired_experiment')
    assert report['conditions']['negative_routing']['mean_delta'] == pytest.approx(-0.1)
    assert paired_transfer(rows, policy=POLICY)['reason'] == 'negative_transfer'
    assert report['policy_sha256'] != paired_transfer(rows, policy=POLICY)['policy_sha256']


def test_the_main_contrast_never_filters_on_observed_or_missing_activation():
    plain = helpful_but_harmful()
    signalled = [dict(r, activated=True, activation_evidence='synthetic-event') if r['arm'] == 'present'
                 and r['task_id'] in ('target0', 'control1') else r for r in plain]
    first, second = paired_transfer(plain, policy=POLICY), paired_transfer(signalled, policy=POLICY)
    for name in ('natural', 'negative_routing'):
        keep = ('matched_roots', 'harmed_roots', 'improved_roots', 'mean_delta')
        assert {k: first['conditions'][name][k] for k in keep} == {k: second['conditions'][name][k] for k in keep}
    assert first['disposition'] == second['disposition'] == 'rejected'
    assert first['conditions']['natural']['present_unknown_activations'] == 4
    assert second['conditions']['natural']['present_observed_activations'] == 1
    # Observed activation on an unrelated control is a false activation; the conditional rate is descriptive.
    assert (first['false_activations'], second['false_activations']) == (0, 1)
    assert second['conditional_on_observed_activation'] == dict(records=2, successes=1, interpretation='descriptive_not_causal')


@pytest.mark.parametrize('claim', [
    pytest.param(dict(activated=True), id='activated-without-evidence'),
    pytest.param(dict(activated=False), id='activated-false'),
    pytest.param(dict(injected_skill_hashes=['a' * 64]), id='hash-injection-only'),
    pytest.param(dict(activated='true', activation_evidence='e'), id='non-boolean-label'),
])
def test_unobserved_activation_is_unknown_never_true_or_false(claim):
    rows = [dict(r, **claim) for r in helpful_but_harmful()]
    report = paired_transfer(rows, policy=POLICY)
    for name in ('natural', 'negative_routing'):
        assert report['conditions'][name]['present_observed_activations'] == 0
        assert report['conditions'][name]['present_unknown_activations'] == 4
        assert report['conditions'][name]['absent_observed_activations'] == 0
    assert report['false_activations'] == 0
    assert report['conditional_on_observed_activation']['records'] == 0


def test_an_observed_activation_in_the_absent_arm_makes_the_comparison_inconclusive():
    rows = pairs('natural', [False] * 3, [True] * 3, 't') + pairs('negative_routing', [True] * 3, [True] * 3, 'c')
    assert paired_transfer(rows, policy=POLICY)['disposition'] == 'uncertified'
    rows[0] = dict(rows[0], activated=True, activation_evidence='shadowed-copy-read')
    report = paired_transfer(rows, policy=POLICY)
    assert (report['disposition'], report['reason']) == ('inconclusive', 'absent_arm_activation')
    assert report['conditions']['natural']['absent_observed_activations'] == 1


def test_preload_is_reported_separately_and_never_decides_the_policy():
    rows = (pairs('natural', [False] * 3, [True] * 3, 't') + pairs('negative_routing', [True] * 3, [True] * 3, 'c')
            + pairs('preload', [True] * 3, [False] * 3, 'p'))
    report = paired_transfer(rows, policy=POLICY)
    assert report['conditions']['preload']['role'] == 'diagnostic'
    assert report['conditions']['preload']['mean_delta'] == -1.0
    assert report['disposition'] == 'uncertified'
    for key in ('target_conditions', 'control_conditions'):
        with pytest.raises(ValueError, match='preload is diagnostic only'):
            paired_transfer(rows, policy=dict(POLICY, **{key: ['preload']}))


def test_unverified_unidentified_and_unmatched_records_are_excluded_and_counted():
    rows = pairs('natural', [False] * 2, [True] * 2, 't') + [
        trial('negative_routing', 'absent', 'c0', True), trial('negative_routing', 'present', 'c0', False, provenance='model_hypothesis'),
        trial('negative_routing', 'absent', 'c1', True), trial('negative_routing', 'present', 'c1', 'unknown'),
        trial('negative_routing', 'present', 'c2', False, root=''), trial('negative_routing', 'absent', 'c3', None)]
    report = paired_transfer(rows, policy=POLICY)
    assert (report['disposition'], report['reason']) == ('inconclusive', 'no_matched_control_roots')
    assert report['excluded'] == dict(unknown_identity_records=1, unverified_records=3)
    control = report['conditions']['negative_routing']
    assert (control['matched_roots'], control['unmatched_roots'], control['mean_delta']) == (0, 2, None)
    target_missing = paired_transfer(pairs('negative_routing', [True], [True], 'c'), policy=POLICY)
    assert (target_missing['disposition'], target_missing['reason']) == ('inconclusive', 'no_matched_target_roots')


def test_repeats_are_averaged_within_a_root_so_the_root_is_the_unit():
    rows = pairs('natural', [False], [True], 't') + [
        trial('negative_routing', 'absent', 'c', True), trial('negative_routing', 'absent', 'c', True),
        trial('negative_routing', 'present', 'c', True), trial('negative_routing', 'present', 'c', False)]
    control = paired_transfer(rows, policy=POLICY)['conditions']['negative_routing']
    assert (control['matched_roots'], control['harmed_roots'], control['mean_delta']) == (1, 1, -0.5)


def test_a_critical_regression_rejects_even_with_target_benefit_and_harmless_controls():
    rows = pairs('natural', [False] * 3, [True] * 3, 't') + pairs('negative_routing', [True] * 3, [True] * 3, 'c')
    rows[-1] = dict(rows[-1], critical_regression=True)
    report = paired_transfer(rows, policy=POLICY)
    assert (report['disposition'], report['reason']) == ('rejected', 'critical_regression')


@pytest.mark.parametrize('policy, message', [
    pytest.param(None, 'exactly', id='missing'),
    pytest.param(dict(POLICY, extra=1), 'exactly', id='extra-key'),
    pytest.param(dict(POLICY, target_conditions=[]), 'must list', id='empty-target'),
    pytest.param(dict(POLICY, control_conditions='negative_routing'), 'must list', id='string-control'),
    pytest.param(dict(POLICY, control_conditions=['natural']), 'both a target and a control', id='overlap'),
    pytest.param(dict(POLICY, max_control_loss=True), 'from 0 to 1', id='boolean-loss'),
    pytest.param(dict(POLICY, max_control_loss=1.5), 'from 0 to 1', id='loss-above-one'),
    pytest.param(dict(POLICY, max_control_loss=-0.1), 'from 0 to 1', id='negative-loss'),
])
def test_the_policy_must_be_fully_declared(policy, message):
    with pytest.raises(ValueError, match=message):
        paired_transfer(helpful_but_harmful(), policy=policy)


@pytest.mark.parametrize('record, message', [
    pytest.param(dict(condition='natural', arm='alternative'), 'arm must be', id='unknown-arm'),
    pytest.param(dict(condition='natural'), 'arm must be', id='missing-arm'),
    pytest.param(dict(condition='unrelated', arm='present'), 'unknown routing condition', id='unknown-condition'),
])
def test_records_must_name_a_known_condition_and_arm(record, message):
    with pytest.raises(ValueError, match=message):
        paired_transfer([record], policy=POLICY)
