"""Synthetic contracts for the schema v2 multi-class triage policy (validation role only)."""
from copy import deepcopy
import hashlib
import json

import pytest

from agent_harness_lab.diagnostics import triage_policy as triage
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from tools import benchmark_models as bench

try:
    from tests.unit.evaluation import test_benchmark_contracts as benchmark_fixtures
except ImportError:
    from tests.unit.evaluation import test_benchmark_contracts as benchmark_fixtures

SEVERE, NORMAL, NONE = 'tool_misuse', 'instruction_drift', 'no_incident'


def policy(**changes):
    value = dict(schema_version=2, profile='diagnostic-triage-policy-v2',
        classes={SEVERE: dict(severity='severe', threshold=.6, min_precision=.5, min_recall=.5),
                 NORMAL: dict(severity='normal', threshold=.6, min_precision=.5, min_recall=.5),
                 NONE: dict(severity='none', threshold=.6, min_precision=.5, min_recall=.5)},
        costs=dict(false_alert=1, wrong_class=2, missed_alert=5, severe_miss=50, review=1),
        review_capacity_roots=10, abstention=dict(min_margin=.1), min_coverage=.5, max_severe_misses=0,
        threshold_provenance='human_preregistered', model_sha256=None, calibrator_sha256=None,
        fit_roots_sha256=[], calibration_roots_sha256=[], threshold_tuning_roots_sha256=[])
    value.update(changes)
    return value


def row(root, label, top, score=.9, **others):
    scores = {SEVERE: 0., NORMAL: 0., NONE: 0., **others}
    if top is not None:
        scores[top] = score
    return dict(root_case_id=root, split='heldout', label=label, scores=scores)


def sha(root):
    return hashlib.sha256(root.encode()).hexdigest()


@pytest.mark.parametrize('known_label', [NONE, None])
def test_coverage_counts_unlabeled_decisions_and_abstentions(known_label):
    decided = row('decided', known_label, NONE)
    abstained = row('unlabeled-abstention', None, None)
    result = triage.evaluate(policy(min_coverage=.75), [decided, abstained, abstained], report_version=3)
    assert result['coverage'] == .5
    assert result['status'] == 'rejected'
    assert 'coverage_below_minimum' in result['reasons']
    assert result['labeled_roots'] == (1 if known_label else 0)
    assert result['decided_labeled_roots'] == (1 if known_label else 0)
    assert not result['automatic_promotion'] and not result['publication_allowed']
    assert not public_export_allowed([result])


def test_unlabeled_decisions_have_coverage_but_no_validation_authority():
    result = triage.evaluate(policy(), [row('unlabeled', None, NONE)], report_version=3)
    assert result['coverage'] == 1
    assert result['labeled_roots'] == 0 and result['decided_labeled_roots'] == 0
    assert result['status'] == 'insufficient_evidence'
    assert result['certification'] == 'not_issued'


def test_legacy_v2_coverage_and_version_validation_are_preserved():
    rows = [row('decided', NONE, NONE), row('unlabeled-abstention', None, None)]
    legacy = triage.evaluate(policy(min_coverage=.75), rows)
    revised = triage.evaluate(policy(min_coverage=.75), rows, report_version=3)
    assert legacy['profile'] == triage.REPORT_PROFILE and legacy['coverage'] == 1
    assert 'coverage_below_minimum' not in legacy['reasons']
    assert revised['profile'] == triage.REPORT_PROFILE_V3 and revised['coverage'] == .5
    with pytest.raises(ValueError, match='report version'):
        triage.evaluate(policy(), rows, report_version=True)


def test_class_wise_root_grouped_confusion_and_costs():
    rows = [row('s1', SEVERE, SEVERE), row('s2', SEVERE, NORMAL), row('n1', NORMAL, NORMAL),
            row('n2', NORMAL, NONE), row('z1', NONE, NONE), row('z2', NONE, SEVERE),
            row('z3', NONE, NONE), row('u1', None, NONE)]
    rows += [row('z1', NONE, NONE)] * 5  # repeated rows of one root count once
    result = triage.evaluate(policy(), rows)
    assert (result['labeled_roots'], result['unknown_roots'], result['coverage']) == (7, 1, 1)
    assert result['confusion'][SEVERE] == {SEVERE: 1, NORMAL: 1, NONE: 0, 'abstained': 0}
    assert result['confusion'][NONE] == {SEVERE: 1, NORMAL: 0, NONE: 2, 'abstained': 0}
    severe = result['classes'][SEVERE]
    assert (severe['tp'], severe['fp'], severe['fn'], severe['tn'], severe['abstained']) == (1, 1, 1, 4, 0)
    assert (severe['precision'], severe['recall']) == (.5, .5)
    assert result['classes'][NONE]['tn'] == 7-2-1-1
    assert result['costs']['counts'] == dict(false_alert=1, wrong_class=0, missed_alert=1, severe_miss=1, review=0)
    assert result['costs']['total'] == 1+5+50
    assert (result['qualification'], result['certification']) == ('descriptive_only', 'not_issued')
    assert not result['automatic_promotion']
    assert (result['role'], result['threshold_selection']) == ('validate', 'refused_in_validation')


def test_severe_misses_stay_visible_when_aggregate_metrics_are_high():
    rows = [row(f'z{i}', NONE, NONE) for i in range(200)] + [row(f'n{i}', NORMAL, NORMAL) for i in range(50)]
    rows += [row(f's{i}', SEVERE, SEVERE) for i in range(20)] + [row('hidden-severe', SEVERE, NONE)]
    result = triage.evaluate(policy(), rows)
    assert result['classes'][SEVERE]['recall'] > .9
    assert result['coverage'] == 1
    misses = result['severe_misses']
    assert (misses['count'], misses['predicted_non_severe'], misses['unreviewed_abstention']) == (1, 1, 0)
    assert misses['roots_sha256'] == [sha('hidden-severe')]
    assert 'severe_misses_above_maximum' in result['reasons']
    assert result['status'] == 'rejected'
    assert 'hidden-severe' not in json.dumps(result)


def test_a_wrong_severe_class_alert_is_a_wrong_class_not_a_severe_miss():
    other = dict(policy()['classes'], second=dict(severity='severe', threshold=.6, min_precision=0, min_recall=0))
    wrong = [dict(r, scores=dict(r['scores'], second=0.)) for r in (row('s', SEVERE, SEVERE), row('t', SEVERE, None))]
    wrong[1]['scores']['second'] = .9
    result = triage.evaluate(policy(classes=other), wrong)
    assert (result['severe_misses']['count'], result['costs']['counts']['wrong_class']) == (0, 1)


def test_review_capacity_overflow_turns_severe_abstentions_into_misses():
    rows = [row('a', SEVERE, SEVERE, .55), row('b', SEVERE, SEVERE, .5), row('c', NONE, NONE, .4),
            row('d', NONE, NONE), row('e', NORMAL, NORMAL), row('f', SEVERE, SEVERE)]
    result = triage.evaluate(policy(review_capacity_roots=1), rows)
    abstentions = result['abstentions']
    assert (abstentions['total'], abstentions['routed_to_review'], abstentions['capacity_overflow']) == (3, 1, 2)
    assert abstentions['by_reason'] == {'below_class_threshold': 3}
    decisions = {d['root_sha256']: d for d in result['decisions']}
    assert decisions[sha('a')]['review']  # label-free queue: highest severe score first
    assert not decisions[sha('b')]['review']
    assert result['severe_misses']['roots_sha256'] == [sha('b')]
    assert result['severe_misses']['unreviewed_abstention'] == 1
    assert result['classes'][SEVERE]['abstained'] == 2
    assert result['classes'][SEVERE]['recall'] == 1/3
    assert 'review_capacity_exceeded' in result['reasons']
    assert result['costs']['counts']['review'] == 1
    enough = triage.evaluate(policy(review_capacity_roots=3), rows)
    assert enough['severe_misses']['count'] == 0
    assert 'review_capacity_exceeded' not in enough['reasons']
    assert f'class:{SEVERE}:recall_below_minimum' in enough['reasons']  # abstentions stay in recall


def test_abstention_rules_and_all_abstain_cannot_qualify():
    tied = row('t', SEVERE, SEVERE, .9, **{NORMAL: .9})
    narrow = row('m', NORMAL, NORMAL, .9, **{NONE: .85})
    conflict = [row('c', NONE, NONE), row('c', NONE, NORMAL)]
    result = triage.evaluate(policy(), [tied, narrow, *conflict])
    assert result['abstentions']['by_reason'] == {'conflicting_root_decisions': 1, 'margin_below_minimum': 1, 'tied_top_classes': 1}
    assert result['coverage'] == 0
    assert 'coverage_below_minimum' in result['reasons']
    assert f'class:{SEVERE}:precision_undefined' in result['reasons']
    assert result['status'] == 'rejected'


def test_a_missing_class_cannot_qualify():
    missing = triage.evaluate(policy(), [row('z', NONE, NONE)])
    assert f'class:{SEVERE}:no_validation_roots' in missing['reasons']
    assert missing['classes'][SEVERE]['recall'] is None
    assert missing['status'] != 'accepted_for_shadow'

VALIDATION_ROWS = [row('p', SEVERE, SEVERE), row('q', NONE, NONE)]


def test_validation_never_selects_thresholds():
    unset = deepcopy(policy()); unset['classes'][SEVERE]['threshold'] = None
    with pytest.raises(ValueError, match='never selects thresholds'):
        triage.evaluate(unset, VALIDATION_ROWS)
    assert not hasattr(triage, 'choose_threshold')


@pytest.mark.parametrize('provenance', ('selected_on_validation', 'fitted', None))
def test_validation_refuses_threshold_provenance_other_than_preregistered(provenance):
    with pytest.raises(ValueError, match='never selects'):
        triage.evaluate(policy(threshold_provenance=provenance), VALIDATION_ROWS)


@pytest.mark.parametrize('role', triage.ROLE_ROOTS)
def test_validation_refuses_roots_reused_from_another_role(role):
    with pytest.raises(ValueError, match=role):
        triage.evaluate(policy(**{role: [sha('q')]}), VALIDATION_ROWS)


def _invalid_policies():
    bad_class = deepcopy(policy()); bad_class['classes'][SEVERE]['severity'] = 'critical'
    nan = deepcopy(policy()); nan['classes'][NONE]['threshold'] = float('nan')
    return (pytest.param(dict(policy(), schema_version=1), id='schema-version'),
            pytest.param(dict(policy(), extra=1), id='extra-key'),
            pytest.param(policy(costs=dict(false_alert=1)), id='partial-costs'),
            pytest.param(policy(review_capacity_roots=True), id='boolean-capacity'),
            pytest.param(policy(max_severe_misses=-1), id='negative-severe-misses'),
            pytest.param(policy(model_sha256='x'), id='bad-model-digest'),
            pytest.param(policy(classes={SEVERE: policy()['classes'][SEVERE]}), id='single-class'),
            pytest.param(bad_class, id='unknown-severity'),
            pytest.param(nan, id='nan-threshold'),
            pytest.param(policy(fit_roots_sha256=[sha('a'), sha('a')]), id='duplicate-fit-roots'),
            pytest.param(policy(abstention={}), id='empty-abstention'))


@pytest.mark.parametrize('value', _invalid_policies())
def test_invalid_policy_refuses(value):
    with pytest.raises(ValueError):
        triage.evaluate(value, [row('p', SEVERE, SEVERE)])


def _invalid_observations():
    first = row('p', SEVERE, SEVERE)
    return (pytest.param([dict(first, split='calibration')], id='calibration-split'),
            pytest.param([dict(first, label='unknown_class')], id='unknown-label'),
            pytest.param([dict(first, scores={SEVERE: .9})], id='partial-scores'),
            pytest.param([row('p', SEVERE, SEVERE, float('nan'))], id='nan-score'),
            pytest.param([row('p', SEVERE, SEVERE, 1.5)], id='score-above-one'),
            pytest.param([row('p', SEVERE, SEVERE), row('p', NONE, SEVERE)], id='conflicting-labels'),
            pytest.param([dict(first, root_case_id='')], id='empty-root'),
            pytest.param([], id='no-rows'))


@pytest.mark.parametrize('value', _invalid_observations())
def test_invalid_observations_refuse(value):
    with pytest.raises(ValueError):
        triage.evaluate(policy(), value)


@pytest.mark.parametrize('name', ('abstained:x', 'abstained', 'a:b'))
def test_class_names_cannot_collide_with_abstention_outcomes(name):
    classes = {name if key == SEVERE else key: spec for key, spec in policy()['classes'].items()}
    scores = {name: .9, NORMAL: .1, NONE: 0.}
    rows = [dict(root_case_id='p', split='heldout', label=name, scores=scores)]
    with pytest.raises(ValueError, match='invalid triage class name'):
        triage.evaluate(policy(classes=classes), rows)


def test_streamed_rows_match_lists_and_labels_cannot_change_decisions():
    rows = [row(f'r{i}', (SEVERE, NORMAL, NONE)[i % 3], (SEVERE, NONE, NORMAL)[i % 3], .5+i % 5/10) for i in range(30)]
    listed = triage.evaluate(policy(), rows)
    assert triage.evaluate(policy(), (r for r in rows)) == listed
    relabeled = triage.evaluate(policy(), (dict(r, label=NONE) for r in rows))
    assert relabeled['confusion'] != listed['confusion']
    assert relabeled['decisions'] == listed['decisions']
    assert relabeled['policy_sha256'] == listed['policy_sha256']


def test_reports_and_policies_are_private_even_with_forged_provenance():
    result = triage.evaluate(policy(), [row('p', SEVERE, SEVERE)])
    assert not result['publication_allowed']
    for forged in (result, {**result, **benchmark_fixtures.record(), 'publication_allowed': True},
                   {**policy(), **benchmark_fixtures.record()}):
        assert not public_export_allowed([forged])


def test_v1_binary_policy_is_unchanged_and_rejects_v2():
    rows = [dict(root_case_id=f'r{i}', label=[True, False, None][i % 3], split='heldout', text='synthetic')
            for i in range(12)] + [dict(root_case_id='r0', label=True, split='heldout', text='synthetic')]
    scores = [(i*7 % 10)/10 for i in range(12)]+[.2]
    v1 = dict(schema_version=1, threshold=.6, min_precision=.5, min_recall=.5, min_coverage=.5)
    result = bench.evaluate_triage_policy(rows, scores, v1)
    assert hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest() == '8866d894c3eeae5eb128294530bb8f81b2c53285fce0aeb278a08f7297ae02bc'
    with pytest.raises(ValueError):
        bench.evaluate_triage_policy(rows, scores, policy())
    with pytest.raises(ValueError):
        triage.evaluate(v1, [])


@pytest.mark.parametrize('value',[10**1000,-10**1000])
def test_extreme_integer_scores_and_thresholds_are_rejected_without_overflow(value):
    with pytest.raises(ValueError,match='score'):
        triage.evaluate(policy(),[row('synthetic',NONE,NONE,score=value)])
    configured=policy();configured['classes'][NONE]['threshold']=value
    with pytest.raises(ValueError,match='constraint'):
        triage.evaluate(configured,[row('synthetic',NONE,NONE)])


def test_finite_cost_overflow_is_explicit_unknown_not_nonfinite_json():
    configured=policy();configured['costs']['false_alert']=1e308
    result=triage.evaluate(configured,[row('synthetic-a',NONE,SEVERE),row('synthetic-b',NONE,SEVERE)])
    assert result['costs']['counts']['false_alert']==2
    assert result['costs']['total'] is None and 'cost_total_unrepresentable' in result['reasons']
    assert result['status'] in ('rejected','insufficient_evidence') and not result['automatic_promotion']
    json.dumps(result,allow_nan=False)


def test_large_integer_cost_remains_exact():
    configured=policy();configured['costs']['false_alert']=10**1000
    result=triage.evaluate(configured,[row('synthetic',NONE,SEVERE)])
    assert result['costs']['total']==10**1000
    assert 'cost_total_unrepresentable' not in result['reasons']
    json.dumps(result,allow_nan=False)


@pytest.mark.parametrize('label',[[],{}])
def test_non_scalar_label_is_rejected_as_taxonomy_mismatch(label):
    with pytest.raises(ValueError,match='label outside'):
        triage.evaluate(policy(),[row('synthetic',label,NONE)])


def test_integer_total_above_json_digit_limit_is_unknown():
    import sys

    limit=sys.get_int_max_str_digits()
    if not limit: pytest.skip('integer string digit limit is disabled')
    configured=policy();configured['costs']['false_alert']=10**(limit-1)
    rows=[row(f'synthetic-{index}',NONE,SEVERE) for index in range(10)]
    result=triage.evaluate(configured,rows)
    assert result['costs']['counts']['false_alert']==10
    assert result['costs']['total'] is None and 'cost_total_unrepresentable' in result['reasons']
    assert not result['automatic_promotion'] and not result['publication_allowed']
    json.dumps(result,allow_nan=False)


@pytest.mark.parametrize(('first','second'),[
    ('fit_roots_sha256','calibration_roots_sha256'),
    ('fit_roots_sha256','threshold_tuning_roots_sha256'),
    ('calibration_roots_sha256','threshold_tuning_roots_sha256'),
])
def test_v4_refuses_declared_cross_role_overlap_without_rewriting_legacy(first,second):
    shared=sha('synthetic-development-root')
    model=sha('synthetic-model')
    configured=policy(model_sha256=model,**{first:[shared],second:[shared]})
    observations=[dict(row('synthetic-heldout',NONE,NONE),model_sha256=model)]
    for version in (2,3):
        assert triage.evaluate(configured,observations,report_version=version)['status']=='insufficient_evidence'
    with pytest.raises(ValueError,match='role roots overlap'):
        triage.evaluate(configured,observations,report_version=4)


def test_v4_declared_disjointness_does_not_establish_independence_or_publication():
    configured=policy(fit_roots_sha256=[sha('synthetic-fit')],
        calibration_roots_sha256=[sha('synthetic-calibration')],threshold_tuning_roots_sha256=[sha('synthetic-tuning')])
    configured['model_sha256']=sha('synthetic-model')
    observations=[dict(r,model_sha256=configured['model_sha256']) for r in (row('synthetic-heldout',NONE,NONE),row('synthetic-unlabeled',None,None))]
    result=triage.evaluate(configured,observations,report_version=4)
    assert result['profile']=='diagnostic-triage-evaluation-v4'
    assert result['role_separation']=='declared_hash_lists_disjoint_only' and result['coverage']==.5
    assert result['root_independence']=='unverified' and result['label_authority']=='unverified'
    assert result['certification']=='not_issued' and not result['automatic_promotion']
    assert not public_export_allowed([{**result,**benchmark_fixtures.record(),'publication_allowed':True}])
    with pytest.raises(ValueError,match='evaluated root appears'):
        triage.evaluate(configured,[dict(row('synthetic-fit',NONE,NONE),model_sha256=configured['model_sha256'])],report_version=4)


def test_v4_requires_policy_and_rows_bound_to_the_same_model_revision():
    bound, other = sha('synthetic-model'), sha('synthetic-other-model')
    observations = [dict(row('synthetic-heldout', NONE, NONE), model_sha256=bound)]
    assert triage.evaluate(policy(model_sha256=bound), observations, report_version=4)['model_sha256'] == bound
    with pytest.raises(ValueError, match='bound to a model revision'):
        triage.evaluate(policy(), observations, report_version=4)
    for rows in ([dict(observations[0], model_sha256=other)], [row('synthetic-heldout', NONE, NONE)]):
        with pytest.raises(ValueError, match='outside the policy scope'):
            triage.evaluate(policy(model_sha256=bound), rows, report_version=4)
        assert triage.evaluate(policy(model_sha256=bound), rows, report_version=3)['status'] == 'insufficient_evidence'


def _v5_policy(**changes):
    return policy(model_sha256=sha('synthetic-model'), **changes)


def _v5_rows(items):
    return [dict(r, model_sha256=sha('synthetic-model')) for r in items]


def test_v5_reports_brier_reliability_and_risk_coverage_without_changing_decisions():
    rows = _v5_rows([row('a', NONE, NONE, .9), row('b', NONE, NONE, .9), row('c', SEVERE, NONE, .9),
                     row('d', SEVERE, None), row('e', None, NONE, .9)])
    v4 = triage.evaluate(_v5_policy(), rows, report_version=4)
    v5 = triage.evaluate(_v5_policy(), rows, report_version=5)
    assert v5['profile'] == 'diagnostic-triage-evaluation-v5'
    assert {k: v for k, v in v5.items() if k not in ('profile', 'schema_version', 'score_diagnostics', 'reasons')} == \
        {k: v for k, v in v4.items() if k not in ('profile', 'schema_version', 'reasons')}
    diag = v5['score_diagnostics']
    assert diag['labeled_roots'] == 4 and diag['authority'] == 'descriptive_only'
    # roots a, b: (0.1)^2 each; c: 0.9^2 + 0.9^2... true class scored 0; d: all-zero scores -> 1.0
    assert diag['multiclass_brier'] == pytest.approx((.01+.01+(.81+1)+1)/4)
    bins = diag['reliability']['bins']
    assert bins[4]['roots'] == 3 and bins[4]['accuracy'] == pytest.approx(2/3) and bins[0]['roots'] == 1
    assert diag['reliability']['sparse_bins'] == 2 and 'reliability_bins_sparse' in v5['reasons']
    curve = diag['risk_coverage']
    assert curve[0]['coverage'] == 1 and curve[0]['error_rate'] == pytest.approx(.5)
    assert curve[4]['coverage'] == .75 and curve[4]['error_rate'] == pytest.approx(1/3)
    json.dumps(v5, allow_nan=False)


def test_v5_repeated_rows_count_once_and_undefined_diagnostics_are_null():
    rows = _v5_rows([row('a', NONE, NONE, .9)]*20)
    diag = triage.evaluate(_v5_policy(), rows, report_version=5)['score_diagnostics']
    assert diag['labeled_roots'] == 1 and diag['reliability']['bins'][4]['roots'] == 1
    unlabeled = triage.evaluate(_v5_policy(), _v5_rows([row('u', None, NONE)]), report_version=5)['score_diagnostics']
    assert unlabeled['multiclass_brier'] is None and unlabeled['risk_coverage'][0]['coverage'] is None
    assert unlabeled['reliability']['bins'][4]['accuracy'] is None


def test_v5_keeps_v4_role_and_model_refusals():
    with pytest.raises(ValueError, match='bound to a model revision'):
        triage.evaluate(policy(), [row('a', NONE, NONE)], report_version=5)
    with pytest.raises(ValueError, match='outside the policy scope'):
        triage.evaluate(_v5_policy(), [row('a', NONE, NONE)], report_version=5)
    shared = sha('synthetic-development-root')
    with pytest.raises(ValueError, match='role roots overlap'):
        triage.evaluate(_v5_policy(fit_roots_sha256=[shared], calibration_roots_sha256=[shared]),
                        _v5_rows([row('a', NONE, NONE)]), report_version=5)


def _v6_policy(**changes):
    scope = dict(model_sha256=sha('synthetic-model'), corpus_sha256=sha('synthetic-corpus'), min_calibration_roots=3)
    return policy(**{**scope, **changes})


def _v6_rows(items):
    return [dict(r, model_sha256=sha('synthetic-model'), corpus_sha256=sha('synthetic-corpus')) for r in items]


def test_v6_adds_data_revision_scope_without_changing_v5_decisions_or_diagnostics():
    rows = _v6_rows([row('a', NONE, NONE), row('b', SEVERE, NONE), row('c', None, None)])
    v5 = triage.evaluate({k: v for k, v in _v6_policy().items() if k not in triage.SCOPE_FIELDS}, rows, report_version=5)
    v6 = triage.evaluate(_v6_policy(), rows, report_version=6)
    assert v6['profile'] == 'diagnostic-triage-evaluation-v6' and v6['corpus_sha256'] == sha('synthetic-corpus')
    ignored = ('profile', 'schema_version', 'policy_sha256', 'corpus_sha256')
    assert {k: v for k, v in v6.items() if k not in ignored} == {k: v for k, v in v5.items() if k not in ignored}
    assert v6['certification'] == 'not_issued' and v6['status'] == 'rejected'
    assert not v6['automatic_promotion'] and not v6['publication_allowed'] and not public_export_allowed([v6])
    assert v6['root_independence'] == 'unverified' and v6['label_authority'] == 'unverified'


def test_scope_fields_belong_to_v6_only():
    rows = _v6_rows([row('a', NONE, NONE)])
    unscoped = {k: v for k, v in _v6_policy().items() if k not in triage.SCOPE_FIELDS}
    with pytest.raises(ValueError, match='invalid triage policy v2 schema'):
        triage.evaluate(unscoped, rows, report_version=6)
    for version in (2, 3, 4, 5):
        with pytest.raises(ValueError, match='invalid triage policy v2 schema'):
            triage.evaluate(_v6_policy(), rows, report_version=version)
    with pytest.raises(ValueError, match='invalid triage policy v2 schema'):
        triage.check_policy(_v6_policy())


@pytest.mark.parametrize('changes', [dict(corpus_sha256=None), dict(corpus_sha256='x'*64), dict(corpus_sha256='A'*64),
                                     dict(min_calibration_roots=-1), dict(min_calibration_roots=True),
                                     dict(min_calibration_roots=1.5), dict(min_calibration_roots=None)])
def test_v6_refuses_an_invalid_corpus_hash_or_calibration_minimum(changes):
    with pytest.raises(ValueError, match='corpus revision must be SHA-256'):
        triage.evaluate(_v6_policy(**changes), _v6_rows([row('a', NONE, NONE)]), report_version=6)


def test_v6_refuses_rows_from_another_or_unstated_corpus_revision():
    rows = _v6_rows([row('a', NONE, NONE)])
    for bad in (dict(rows[0], corpus_sha256=sha('synthetic-other-corpus')), {k: v for k, v in rows[0].items() if k != 'corpus_sha256'}):
        with pytest.raises(ValueError, match='corpus revision is outside the policy scope'):
            triage.evaluate(_v6_policy(), [bad], report_version=6)
    with pytest.raises(ValueError, match='outside the policy scope'):
        triage.evaluate(_v6_policy(), [dict(rows[0], model_sha256=sha('synthetic-other-model'))], report_version=6)
    with pytest.raises(ValueError, match='bound to a model revision'):
        triage.evaluate(_v6_policy(model_sha256=None), rows, report_version=6)


def test_v6_rejects_a_declared_calibrator_fitted_on_too_few_declared_roots():
    rows = _v6_rows([row('a', NONE, NONE)])
    fitted = sha('synthetic-calibrator')
    tiny = triage.evaluate(_v6_policy(calibrator_sha256=fitted, calibration_roots_sha256=[sha('c1'), sha('c2')]), rows, report_version=6)
    enough = triage.evaluate(_v6_policy(calibrator_sha256=fitted, calibration_roots_sha256=[sha('c1'), sha('c2'), sha('c3')]), rows, report_version=6)
    assert tiny['status'] == 'rejected' and 'calibration_roots_below_minimum' in tiny['reasons']
    assert enough['status'] == 'insufficient_evidence' and 'calibration_roots_below_minimum' not in enough['reasons']
    # The same declared lists cannot fail a policy that declares no calibrator, or one that preregistered no minimum.
    assert 'calibration_roots_below_minimum' not in triage.evaluate(_v6_policy(), rows, report_version=6)['reasons']
    none = triage.evaluate(_v6_policy(calibrator_sha256=fitted, min_calibration_roots=0), rows, report_version=6)
    assert 'calibration_roots_below_minimum' not in none['reasons']
    # Earlier profiles never apply the check, so their reports stay comparable.
    legacy = {k: v for k, v in _v6_policy(calibrator_sha256=fitted).items() if k not in triage.SCOPE_FIELDS}
    assert 'calibration_roots_below_minimum' not in triage.evaluate(legacy, rows, report_version=5)['reasons']


def test_validation_labels_change_the_report_but_never_the_frozen_policy_artifacts():
    frozen = _v6_policy(fit_roots_sha256=[sha('f')], calibration_roots_sha256=[sha('c')], threshold_tuning_roots_sha256=[sha('t')])
    before = deepcopy(frozen)
    agreeing = _v6_rows([row('a', NONE, NONE), row('b', SEVERE, SEVERE)])
    relabeled = _v6_rows([row('a', SEVERE, NONE), row('b', NONE, SEVERE)])
    first = triage.evaluate(frozen, agreeing, report_version=6)
    second = triage.evaluate(frozen, relabeled, report_version=6)
    assert frozen == before
    assert first['classes'] != second['classes'] and first['confusion'] != second['confusion']
    for key in ('policy_sha256', 'model_sha256', 'calibrator_sha256', 'corpus_sha256', 'decisions', 'decision_threshold_source', 'threshold_selection'):
        assert first[key] == second[key]
    # Roots declared as tuning data cannot reach the final labels: the evaluation refuses them.
    tuning = _v6_policy(threshold_tuning_roots_sha256=[sha('tuning-root')])
    with pytest.raises(ValueError, match='threshold_tuning_roots_sha256'):
        triage.evaluate(tuning, _v6_rows([row('tuning-root', NONE, NONE)]), report_version=6)
