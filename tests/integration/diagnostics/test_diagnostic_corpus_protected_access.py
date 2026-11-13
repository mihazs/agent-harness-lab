"""Synthetic one-shot validation access for the private diagnostic corpus; no model, private traces or gold claims."""
from copy import deepcopy

import pytest

from agent_harness_lab.diagnostics import triage_policy
from agent_harness_lab.diagnostics.corpus import CorpusStore, development_export
from tests.integration.diagnostics.test_diagnostic_corpus import label, sample

MODEL = 'c'*64
FAILURE, SUCCESS, OUTAGE, CHANGED = 'task_failure', 'successful', 'external_failure', 'requirements_changed'


def policy(**changes):
    classes = {FAILURE: 'severe', SUCCESS: 'none', OUTAGE: 'normal', CHANGED: 'normal'}
    value = dict(schema_version=2, profile='diagnostic-triage-policy-v2',
        classes={name: dict(severity=severity, threshold=.6, min_precision=.5, min_recall=.5) for name, severity in classes.items()},
        costs=dict(false_alert=1, wrong_class=2, missed_alert=5, severe_miss=50, review=1), review_capacity_roots=10,
        abstention=dict(min_margin=.1), min_coverage=.5, max_severe_misses=0, threshold_provenance='human_preregistered',
        model_sha256=MODEL, calibrator_sha256=None, fit_roots_sha256=[], calibration_roots_sha256=[], threshold_tuning_roots_sha256=[])
    value.update(changes)
    return value


def scored(top):
    return {name: .9 if name == top else 0. for name in (FAILURE, SUCCESS, OUTAGE, CHANGED)}


@pytest.fixture
def store(tmp_path):
    return CorpusStore(tmp_path/'corpus')


def adjudicate(store, name, partition='validation', outcome=FAILURE, root=None):
    value = sample(name, partition); value['summary'] = f'Synthetic {name} terminal record for an offline access control.'
    if root is not None: value['root_id'] = root
    annotation = label(outcome, 'unrecovered' if outcome == FAILURE else 'not_applicable')
    item = store.enqueue(value)
    for reviewer in ('synthetic-reviewer-one', 'synthetic-reviewer-two'):
        item = store.update(item['case_id'], item['event_sha256'], 'claimed', reviewer=reviewer)
        item = store.update(item['case_id'], item['event_sha256'], 'submitted', reviewer=reviewer, label=annotation)
    return store.update(item['case_id'], item['event_sha256'], 'adjudicated', reviewer='synthetic-adjudicator',
                        label=annotation, reason='Synthetic adjudication basis')


def corpus(store):
    return {name: adjudicate(store, name, partition, outcome) for name, partition, outcome in (
        ('fit', 'fitting', FAILURE), ('val-failure', 'validation', FAILURE), ('val-success', 'validation', SUCCESS),
        ('cert', 'certification', SUCCESS))}


def evaluate(store, plan, top=None, **changes):
    top = top or {}
    args = dict(policy=policy(), model_sha256=MODEL, scores={c['case_id']: scored(top.get(c['case_id'], FAILURE)) for c in plan['cases']})
    args.update(changes)
    return store.evaluate_protected(plan['plan_sha256'], args['policy'], args['model_sha256'], args['scores'])


def revision(store): return store.snapshot()['revision_sha256']


def test_inputs_are_label_free_and_cover_only_the_validation_partition(store):
    items = corpus(store); pending = store.enqueue(dict(sample('val-pending', 'validation'), summary='Synthetic pending record.'))
    plan = store.protected_inputs()
    assert {c['case_id'] for c in plan['cases']} == {items['val-failure']['case_id'], items['val-success']['case_id'], pending['case_id']}
    assert all(set(c) == {'case_id', 'text'} for c in plan['cases'])
    assert 'SUGGESTED_LABEL_MUST_STAY_HIDDEN' not in str(plan) and 'root_id' not in str(plan)
    assert not plan['publication_allowed']


def test_evaluation_reports_the_frozen_policy_and_retires_each_labeled_case(store):
    items = corpus(store); plan = store.protected_inputs()
    result = evaluate(store, plan, {items['val-success']['case_id']: SUCCESS})
    report, access = result['evaluation'], result['access']
    assert report['profile'] == 'diagnostic-triage-evaluation-v5' and report['labeled_roots'] == 2
    assert report['confusion'][FAILURE][FAILURE] == 1 and report['confusion'][SUCCESS][SUCCESS] == 1
    assert (report['qualification'], report['certification']) == ('descriptive_only', 'not_issued')
    assert access['labeled_cases'] == 2 and access['unlabeled_cases'] == 0 and access['model_sha256'] == MODEL
    assert (access['gold_label'], access['classifier_selection_allowed'], access['publication_allowed']) == (False,) * 3
    assert (access['reviewer_authentication'], access['root_independence']) == ('operator_attested', 'unverified')
    assert 'SUGGESTED_LABEL_MUST_STAY_HIDDEN' not in str(result) and 'terminal record' not in str(result)
    assert {i['partition'] for i in store.snapshot()['items'] if i['case_id'] in access['retired_case_ids']} == {'development'}
    certification = next(i for i in store.snapshot()['items'] if i['case_id'] == items['cert']['case_id'])
    assert certification['partition'] == 'certification'
    with pytest.raises(ValueError, match='no untouched validation cases'): store.protected_inputs()
    exported = store.export_development(revision(store))
    retired = [r for r in exported['records'] if r['record']['partition'] == 'validation']
    assert len(retired) == 2 and all(r['partition'] == 'development' and access['access_id'] in r['retirement']['reason'] for r in retired)
    development_export(exported)


def test_a_label_supports_one_evaluation(store):
    corpus(store); plan = store.protected_inputs(); evaluate(store, plan)
    with pytest.raises(ValueError, match='no untouched validation cases'): evaluate(store, plan)


def test_rejected_input_consumes_nothing(store):
    corpus(store); plan = store.protected_inputs(); before = revision(store)
    ids = [c['case_id'] for c in plan['cases']]; good = {i: scored(FAILURE) for i in ids}
    bad_policy = policy(classes={k: v for k, v in policy()['classes'].items() if k != SUCCESS})
    overlap = policy(fit_roots_sha256=[triage_policy.root_sha256('val-failure-root')])
    nan = {**good, ids[0]: {**scored(FAILURE), FAILURE: float('nan')}}
    for changes, match in [(dict(scores={ids[0]: scored(FAILURE)}), 'exactly the frozen'), (dict(scores={**good, 'f'*64: scored(FAILURE)}), 'exactly the frozen'),
            (dict(scores=nan), 'invalid triage class score'), (dict(policy=bad_policy), 'exactly the corpus terminal outcomes'),
            (dict(policy=overlap), 'untouched'), (dict(model_sha256='d'*64), 'model revision'),
            (dict(policy=policy(model_sha256=None)), 'model revision')]:
        with pytest.raises(ValueError, match=match): evaluate(store, plan, **changes)
        assert revision(store) == before and store.protected_inputs()['plan_sha256'] == plan['plan_sha256']
    with pytest.raises(ValueError, match='stale'): store.evaluate_protected('e'*64, policy(), MODEL, good)
    assert revision(store) == before
    assert evaluate(store, plan)['access']['labeled_cases'] == 2


def test_unreviewed_unknown_and_mixed_roots_stay_protected_and_unlabeled(store):
    adjudicate(store, 'val-known'); store.enqueue(dict(sample('val-pending', 'validation'), summary='Synthetic pending record.'))
    unknown = dict(sample('val-unknown', 'validation'), summary='Synthetic unknown record.', capture_complete=False)
    item = store.enqueue(unknown); item = store.update(item['case_id'], item['event_sha256'], 'claimed', reviewer='one')
    cleared = label('unknown', 'unknown'); cleared.update(verification='unknown', evidence_refs=[], symptoms=[])
    store.update(item['case_id'], item['event_sha256'], 'submitted', reviewer='one', label=cleared)
    adjudicate(store, 'val-mixed-first', root='shared-root'); store.enqueue(dict(sample('val-mixed-second', 'validation'),
        root_id='shared-root', summary='Synthetic second record of the shared root.'))
    plan = store.protected_inputs(); assert len(plan['cases']) == 5
    result = evaluate(store, plan); access, report = result['access'], result['evaluation']
    assert (access['labeled_cases'], access['unlabeled_cases'], report['labeled_roots'], report['unknown_roots']) == (1, 4, 1, 3)
    later = store.protected_inputs()
    assert len(later['cases']) == 4 and not {c['case_id'] for c in later['cases']} & set(access['retired_case_ids'])


def test_conflicting_outcomes_in_one_root_stay_unlabeled_without_a_rejection(store):
    adjudicate(store, 'val-first', root='shared-root'); adjudicate(store, 'val-second', outcome=SUCCESS, root='shared-root')
    result = evaluate(store, store.protected_inputs())
    assert (result['access']['labeled_cases'], result['evaluation']['labeled_roots'], result['evaluation']['unknown_roots']) == (0, 0, 1)
    assert len(store.protected_inputs()['cases']) == 2


def test_validation_labels_change_the_report_but_never_the_supplied_scores(tmp_path):
    reports = []
    for name, outcome in (('flipped', SUCCESS), ('kept', FAILURE)):
        other = CorpusStore(tmp_path/name)
        for case, partition, label_ in (('fit', 'fitting', FAILURE), ('val-failure', 'validation', outcome), ('val-success', 'validation', SUCCESS)):
            adjudicate(other, case, partition, label_)
        plan = other.protected_inputs(); scores = {c['case_id']: scored(FAILURE) for c in plan['cases']}
        frozen = deepcopy(scores); result = evaluate(other, plan, scores=scores)
        assert scores == frozen and result['access']['model_sha256'] == MODEL
        reports.append(result['evaluation']['confusion'])
    assert reports[0][SUCCESS][FAILURE] == 2 and reports[1][SUCCESS][FAILURE] == 1 and reports[1][FAILURE][FAILURE] == 1


def test_label_changes_invalidate_the_frozen_plan(store):
    items = corpus(store); plan = store.protected_inputs()
    changed = dict(sample('val-failure', 'validation'), summary='Synthetic val-failure terminal record for an offline access control.',
                   rubric_sha256='b'*64, supersedes=items['val-failure']['case_id'])
    store.enqueue(changed)
    with pytest.raises(ValueError, match='stale protected access plan'): evaluate(store, plan)


def test_copied_text_with_a_new_root_blocks_protected_access(store):
    corpus(store); copied = sample('copy', 'fitting'); copied['summary'] = 'Synthetic val-failure terminal record for an offline access control.'
    store.enqueue(copied)
    with pytest.raises(ValueError, match='text aliases'): store.protected_inputs()
    before = revision(store)
    with pytest.raises(ValueError, match='text aliases'): store.evaluate_protected('e'*64, policy(), MODEL, {})
    assert revision(store) == before


def test_certification_is_not_released_through_this_access(store):
    adjudicate(store, 'cert-only', 'certification', SUCCESS)
    with pytest.raises(ValueError, match='no untouched validation cases'): store.protected_inputs()
