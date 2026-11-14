"""Synthetic contracts for the versioned audit summary consumed by monitoring and the triage gate (#75)."""
from copy import deepcopy
import json

import pytest

from agent_harness_lab.diagnostics import audit_decisions as decisions, audit_sampling as audit, audit_summary as summary_api
from agent_harness_lab.diagnostics import triage_policy
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from tests.unit.diagnostics.test_audit_decisions import FIRST, adapt, budget, chain
from tests.unit.diagnostics.test_audit_sampling import COMMON, RARE, frame, labels, plan, unit
from tests.unit.diagnostics.test_triage_policy_v2 import policy as triage_fixture

MODEL = 'c' * 64


@pytest.fixture(scope='module')
def triage():
    return triage_fixture(model_sha256=MODEL)


@pytest.fixture(scope='module')
def sample():
    """200 units; the classifier alerts only on the common class and never on 10 rare incidents."""
    units, truth = [], {}
    for i in range(200):
        name = COMMON if i < 10 else RARE if i < 20 else audit.NO_INCIDENT
        units.append(unit(f's{i}', alerted=i < 10))
        truth[audit.unit_sha256(f's{i}')] = name
    value = frame(units)
    return audit.draw(value, plan(value, rates=dict(random=.5, alerted=.5), budget=200)), truth


def observation(triage, receipt, report, composition=False):
    """The monitor's observation shape around a real estimate, without the private corpus."""
    if composition:
        report = dict(schema_version=1, profile='audit-corpus-composition-observation-v1', audit_report=report)
    return dict(schema_version=1, profile='triage-audit-monitor-composition-v1' if composition
                else 'triage-audit-monitor-observation-v1', policy_sha256=triage_policy.policy_sha256(triage),
                model_sha256=triage['model_sha256'], audit_receipt_sha256=receipt['receipt_sha256'], audit_report=report,
                policy_application='not_observed', **summary_api.FIXED)


def reseal(value):
    body = {key: item for key, item in value.items() if key != 'summary_sha256'}
    return dict(body, summary_sha256=audit._digest(body))


def test_misses_of_a_rare_class_reach_the_summary_without_unit_identities(sample, triage):
    receipt, truth = sample
    report = audit.estimate(receipt, labels(receipt, truth))
    result = summary_api.summarize(observation(triage, receipt, report))
    assert (result['status'], result['design_profile'], result['estimand']) == ('estimated', audit.REPORT_PROFILE, audit.ESTIMAND)
    assert result['observed_false_negatives'] > 0 and result['estimates']['missed_incidents']['sampled'] > 0
    assert result['estimates'] == report['estimates'] and result['alert_only']['population_support'] == 'unsupported'
    assert result['decision_log']['status'] == 'not_supplied' and 'decision_log_not_supplied' in result['limitations']
    assert 'reviewer_labels_unauthenticated' in result['limitations'] and len(set(result['limitations'])) == len(result['limitations'])
    assert (result['action'], result['promotion_authority']) == ('observe_only', 'none')
    assert not result['automatic_policy_update'] and not result['automatic_rollback'] and not public_export_allowed([result])
    text = json.dumps(result)
    assert not any(item['unit_sha256'] in text for item in receipt['draws']) and summary_api.check(result, triage) is result
    assert summary_api.summarize(observation(triage, receipt, report, composition=True)) == result


def test_pending_reviews_stay_inconclusive_with_bounds_and_no_estimates(sample, triage):
    receipt, truth = sample
    report = audit.estimate(receipt, labels(receipt, truth, ['pending']))
    result = summary_api.summarize(observation(triage, receipt, report))
    assert (result['status'], result['estimates'], result['reasons']) == ('inconclusive', None, ['pending_labels'])
    assert result['missing_label_bounds'] == report['missing_label_bounds'] and result['review_status']['pending'] == 1
    assert summary_api.check(result, triage) is result


def test_decision_log_before_labels_is_kept_and_a_late_decision_invalidates_every_estimate(sample, triage):
    receipt, truth = sample
    observed = observation(triage, receipt, audit.estimate(receipt, labels(receipt, truth)))
    log = chain(budget('2026-01-08T00:30:00+00:00', 150, 200), adapt('2026-01-08T00:40:00+00:00', 'b' * 64, receipt['plan_sha256']))
    logged = summary_api.summarize(observed, decisions.check(receipt, log, FIRST))
    assert logged['status'] == 'estimated' and logged['estimates'] is not None
    assert logged['decision_log'] == dict(status='logged_before_labels', head_sha256=decisions.entry_sha256(log[-1]), entries=2)
    assert 'decision_log_not_supplied' not in logged['limitations'] and 'log_completeness_unverified' in logged['limitations']
    late = summary_api.summarize(observed, decisions.check(receipt, chain(budget(FIRST, 200, 200)), FIRST))
    assert (late['status'], late['reasons'], late['estimates'], late['missing_label_bounds']) == (
        'invalid', ['decision_at_or_after_first_label'], None, None)
    assert summary_api.check(late, triage) is late


@pytest.mark.parametrize('mutate', [
    lambda o: o.update(profile='other'), lambda o: o.update(action='apply'), lambda o: o.update(promotion_authority='granted'),
    lambda o: o.update(automatic_policy_update=True), lambda o: o.update(publication_allowed=True), lambda o: o.pop('identity_binding'),
    lambda o: o.update(audit_receipt_sha256='d' * 64),
])
def test_an_observation_that_claims_authority_or_another_receipt_is_refused(sample, triage, mutate):
    receipt, truth = sample
    observed = observation(triage, receipt, audit.estimate(receipt, labels(receipt, truth)))
    mutate(observed)
    with pytest.raises(ValueError):
        summary_api.summarize(observed)


def test_a_decision_report_for_another_receipt_or_with_altered_claims_is_refused(sample, triage):
    receipt, truth = sample
    observed = observation(triage, receipt, audit.estimate(receipt, labels(receipt, truth)))
    good = decisions.check(receipt, [], FIRST)
    for change in (dict(receipt_sha256='d' * 64), dict(status='invalid'), dict(design_based_estimates_supported=False),
                   dict(automatic_policy_update=True), dict(profile='other'), dict(extra=1)):
        with pytest.raises(ValueError):
            summary_api.summarize(observed, dict(good, **change))
    with pytest.raises(ValueError):
        summary_api.summarize(observed, 'report')


@pytest.mark.parametrize('mutate', [
    lambda s: s.update(estimates=None), lambda s: s.update(status='inconclusive'), lambda s: s.update(status='invalid'),
    lambda s: s.update(automatic_policy_update=True), lambda s: s.update(promotion_authority='granted'),
    lambda s: s.update(profile='other'), lambda s: s.update(schema_version=2), lambda s: s.update(extra=1),
    lambda s: s.update(decision_log='logged'), lambda s: s.pop('limitations'),
])
def test_a_tampered_or_inconsistent_summary_is_refused(sample, triage, mutate):
    receipt, truth = sample
    result = summary_api.summarize(observation(triage, receipt, audit.estimate(receipt, labels(receipt, truth))))
    altered = deepcopy(result)
    mutate(altered)
    with pytest.raises(ValueError):
        summary_api.check(altered, triage)  # a stale seal
    if 'extra' not in altered and 'limitations' in altered:
        with pytest.raises(ValueError):
            summary_api.check(reseal(altered), triage)  # a consistent seal cannot hide inconsistent claims


def test_a_summary_for_another_policy_or_model_is_refused(sample, triage):
    receipt, truth = sample
    result = summary_api.summarize(observation(triage, receipt, audit.estimate(receipt, labels(receipt, truth))))
    for other in (triage_fixture(model_sha256='d' * 64), triage_fixture(model_sha256=MODEL, review_capacity_roots=11),
                  triage_fixture(model_sha256=None)):
        with pytest.raises(ValueError, match='policy or model'):
            summary_api.check(result, other)
    with pytest.raises(ValueError):
        summary_api.check(result, dict(triage, schema_version=1))
