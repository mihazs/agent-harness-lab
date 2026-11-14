"""Synthetic contracts for the preregistered requirement gate over the audit summary (#75)."""
from copy import deepcopy
import json

import pytest

from agent_harness_lab.diagnostics import audit_decisions as decisions, audit_fixed as fixed, audit_gate as gate
from agent_harness_lab.diagnostics import audit_sampling as audit, audit_summary as summary_api
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from tests.unit.diagnostics.test_audit_decisions import FIRST, budget, chain
from tests.unit.diagnostics.test_audit_fixed import labels as fixed_labels, policy as fixed_plan
from tests.unit.diagnostics.test_audit_sampling import COMMON, RARE, frame, labels, plan, unit
from tests.unit.diagnostics.test_audit_summary import MODEL, observation, reseal
from tests.unit.diagnostics.test_triage_policy_v2 import policy as triage_fixture

# The fixture's missed-incident rate is 0.05 with a 95% interval of [0.025, 0.095], and its recall
# is 0.44 with [0.28, 0.62]; each limit below is chosen against one of those bounds.


def requirement(**changes):
    return dict(dict(schema_version=1, profile=gate.REQUIREMENT_PROFILE, min_reviewed=1, max_missed_incident_rate=.1,
                     min_triage_recall=None, require_decision_log=False), **changes)


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


def summarize(triage, receipt, report, log=None):
    return summary_api.summarize(observation(triage, receipt, report), log)


@pytest.fixture(scope='module')
def estimated(sample, triage):
    receipt, truth = sample
    return summarize(triage, receipt, audit.estimate(receipt, labels(receipt, truth)))


@pytest.mark.parametrize(('limits', 'status', 'reasons'), [
    pytest.param(dict(max_missed_incident_rate=.1), 'requirement_met', [], id='rate-interval-below-maximum'),
    pytest.param(dict(max_missed_incident_rate=.095), 'requirement_met', [], id='rate-upper-limit-equals-maximum'),
    pytest.param(dict(max_missed_incident_rate=.02), 'requirement_not_met', ['missed_incident_rate_not_met'],
                 id='rate-interval-above-maximum'),
    pytest.param(dict(max_missed_incident_rate=.05), 'inconclusive', ['missed_incident_rate_interval_straddles_limit'],
                 id='rate-interval-straddles-maximum'),
    pytest.param(dict(min_triage_recall=.2), 'requirement_met', [], id='recall-interval-above-minimum'),
    pytest.param(dict(min_triage_recall=.7), 'requirement_not_met', ['triage_recall_not_met'], id='recall-interval-below-minimum'),
    pytest.param(dict(min_triage_recall=.4), 'inconclusive', ['triage_recall_interval_straddles_limit'],
                 id='recall-interval-straddles-minimum'),
    pytest.param(dict(max_missed_incident_rate=.02, min_triage_recall=.4), 'requirement_not_met',
                 ['missed_incident_rate_not_met', 'triage_recall_interval_straddles_limit'], id='one-broken-limit-outweighs-an-open-one'),
    pytest.param(dict(max_missed_incident_rate=.05, min_triage_recall=.2), 'inconclusive',
                 ['missed_incident_rate_interval_straddles_limit'], id='one-open-limit-outweighs-a-met-one'),
])
def test_a_limit_is_met_or_broken_only_when_the_whole_interval_says_so(estimated, triage, limits, status, reasons):
    result = gate.evaluate(estimated, triage, requirement(**limits))
    assert (result['status'], result['reasons']) == (status, reasons)
    assert result['criteria']['missed_incident_rate']['ci95'] == estimated['estimates']['missed_incidents']['rate']['ci95']


def test_the_result_is_descriptive_private_and_bound_to_the_summary_policy_and_requirement(estimated, triage, sample):
    receipt, _ = sample
    result = gate.evaluate(estimated, triage, requirement())
    assert (result['summary_sha256'], result['audit_receipt_sha256'], result['model_sha256']) == (
        estimated['summary_sha256'], receipt['receipt_sha256'], MODEL)
    assert result['requirement_sha256'] == gate.requirement_sha256(requirement())
    assert result['requirement_sha256'] != gate.evaluate(estimated, triage, requirement(min_reviewed=2))['requirement_sha256']
    assert (result['action'], result['promotion_authority'], result['certification']) == ('observe_only', 'none', 'not_issued')
    assert not result['automatic_policy_update'] and not result['automatic_rollback'] and not public_export_allowed([result])
    assert set(estimated['limitations']) <= set(result['limitations']) and 'requirement_preregistration_unverified' in result['limitations']
    assert not any(item['unit_sha256'] in json.dumps(result) for item in receipt['draws'])


def test_pending_reviews_stay_inconclusive_even_when_the_missing_label_bounds_would_pass(sample, triage):
    receipt, truth = sample
    summary = summarize(triage, receipt, audit.estimate(receipt, labels(receipt, truth, ['pending'])))
    assert summary['missing_label_bounds']['missed_incident_rate'][1] < 1
    result = gate.evaluate(summary, triage, requirement(max_missed_incident_rate=1))
    assert (result['status'], result['reasons'], result['criteria']) == ('inconclusive', ['pending_labels'], None)


def test_a_decision_after_the_first_label_makes_the_result_invalid(sample, triage):
    receipt, truth = sample
    late = decisions.check(receipt, chain(budget(FIRST, 200, 200)), FIRST)
    summary = summarize(triage, receipt, audit.estimate(receipt, labels(receipt, truth)), late)
    result = gate.evaluate(summary, triage, requirement(max_missed_incident_rate=1))
    assert (result['status'], result['reasons'], result['criteria']) == ('invalid', ['decision_at_or_after_first_label'], None)


def test_a_required_decision_log_must_be_supplied_and_precede_the_labels(sample, triage, estimated):
    receipt, truth = sample
    needs_log = requirement(require_decision_log=True)
    missing = gate.evaluate(estimated, triage, needs_log)
    assert (missing['status'], missing['reasons']) == ('inconclusive', ['decision_log_not_supplied'])
    logged = summarize(triage, receipt, audit.estimate(receipt, labels(receipt, truth)), decisions.check(receipt, [], FIRST))
    assert gate.evaluate(logged, triage, needs_log)['status'] == 'requirement_met'
    assert gate.evaluate(estimated, triage, requirement())['status'] == 'requirement_met'


@pytest.mark.parametrize(('minimum', 'status', 'reasons'), [
    pytest.param(101, 'requirement_met', [], id='reviewed-equals-minimum'),
    pytest.param(102, 'inconclusive', ['reviewed_below_minimum'], id='reviewed-below-minimum'),
    pytest.param(10_000, 'inconclusive', ['reviewed_below_minimum'], id='oversized-minimum'),
])
def test_too_few_reviewed_draws_stay_inconclusive_without_limit_verdicts(estimated, triage, minimum, status, reasons):
    assert estimated['reviewed'] == 101
    result = gate.evaluate(estimated, triage, requirement(min_reviewed=minimum))
    assert (result['status'], result['reasons']) == (status, reasons)
    assert (result['criteria'] is None) == bool(reasons)


def test_an_undefined_recall_is_never_a_pass_or_a_failure(triage):
    units = [unit(f'n{i}') for i in range(60)]
    value = frame(units)
    receipt = audit.draw(value, plan(value, rates=dict(random=.5), budget=60))
    report = audit.estimate(receipt, labels(receipt, {audit.unit_sha256(f'n{i}'): audit.NO_INCIDENT for i in range(60)}))
    summary = summarize(triage, receipt, report)
    assert summary['estimates']['triage_recall'] is None
    result = gate.evaluate(summary, triage, requirement(min_triage_recall=.5))
    assert (result['status'], result['reasons']) == ('inconclusive', ['triage_recall_undefined'])
    assert result['criteria']['missed_incident_rate']['verdict'] == 'met' and result['criteria']['triage_recall']['verdict'] == 'inconclusive'
    assert gate.evaluate(summary, triage, requirement())['status'] == 'requirement_met'


@pytest.mark.parametrize(('limits', 'status'), [
    pytest.param(dict(max_missed_incident_rate=.1, min_triage_recall=.5), 'requirement_met', id='census-at-both-limits'),
    pytest.param(dict(max_missed_incident_rate=.05), 'requirement_not_met', id='census-rate-above-maximum'),
    pytest.param(dict(min_triage_recall=.6), 'requirement_not_met', id='census-recall-below-minimum'),
])
def test_a_finite_design_summary_is_judged_on_its_own_estimate_shape(triage, limits, status):
    units = [unit('a', alerted=True), unit('b'), *[unit(f'c{i}') for i in range(8)]]
    value = frame(units)
    plan_value = fixed_plan(value, k=10, budget=10)
    receipt = fixed.draw(value, plan_value)
    incidents = {audit.unit_sha256('a'), audit.unit_sha256('b')}
    summary = summarize(triage, receipt, fixed.estimate(receipt, value, plan_value, fixed_labels(receipt, incidents)))
    assert summary['design_profile'] == fixed.REPORT_PROFILE
    result = gate.evaluate(summary, triage, requirement(**limits))
    assert result['status'] == status and result['criteria']['missed_incident_rate']['ci95'] == [.1, .1]


@pytest.mark.parametrize('changes', [
    pytest.param(dict(schema_version=2), id='schema-version'), pytest.param(dict(schema_version=True), id='boolean-schema-version'),
    pytest.param(dict(profile='other'), id='profile'), pytest.param(dict(min_reviewed=0), id='zero-minimum'),
    pytest.param(dict(min_reviewed=-1), id='negative-minimum'), pytest.param(dict(min_reviewed=True), id='boolean-minimum'),
    pytest.param(dict(min_reviewed=1.5), id='fractional-minimum'), pytest.param(dict(max_missed_incident_rate=None), id='missing-maximum'),
    pytest.param(dict(max_missed_incident_rate=1.1), id='oversized-maximum'), pytest.param(dict(max_missed_incident_rate=-.1), id='negative-maximum'),
    pytest.param(dict(max_missed_incident_rate=float('nan')), id='nan-maximum'), pytest.param(dict(max_missed_incident_rate=True), id='boolean-maximum'),
    pytest.param(dict(min_triage_recall=2), id='oversized-recall'), pytest.param(dict(min_triage_recall='.5'), id='text-recall'),
    pytest.param(dict(require_decision_log=1), id='non-boolean-log-flag'), pytest.param(dict(extra=1), id='unknown-field'),
])
def test_an_invalid_requirement_is_refused_before_any_verdict(estimated, triage, changes):
    with pytest.raises(ValueError, match='audit requirement'):
        gate.evaluate(estimated, triage, requirement(**changes))


def test_a_requirement_missing_a_field_or_not_a_mapping_is_refused(estimated, triage):
    incomplete = requirement()
    del incomplete['require_decision_log']
    for value in (incomplete, None, 'requirement', []):
        with pytest.raises(ValueError, match='audit requirement'):
            gate.evaluate(estimated, triage, value)


def test_an_altered_summary_or_one_for_another_policy_is_refused(estimated, triage):
    altered = deepcopy(estimated)
    altered['estimates']['missed_incidents']['rate']['ci95'] = [0.0, 0.0]
    with pytest.raises(ValueError, match='altered'):
        gate.evaluate(altered, triage, requirement())
    with pytest.raises(ValueError, match='policy or model'):
        gate.evaluate(estimated, triage_fixture(model_sha256='d' * 64), requirement())


@pytest.mark.parametrize('damage', [
    pytest.param(lambda e: e['missed_incidents']['rate'].update(ci95=[.5, .1]), id='inverted-interval'),
    pytest.param(lambda e: e['missed_incidents']['rate'].update(ci95=[None, .1]), id='null-limit'),
    pytest.param(lambda e: e['missed_incidents']['rate'].update(ci95=[0, 1, 1]), id='three-limits'),
    pytest.param(lambda e: e['missed_incidents']['rate'].update(ci95=[0, 2]), id='limit-above-one'),
    pytest.param(lambda e: e['missed_incidents']['rate'].update(ci95=[0, '1']), id='text-limit'),
    pytest.param(lambda e: e['missed_incidents']['rate'].pop('ci95'), id='missing-interval'),
    pytest.param(lambda e: e.pop('missed_incidents'), id='missing-missed-incident-estimate'),
    pytest.param(lambda e: e.update(triage_recall=dict(estimate=.5, ci95='wide')), id='text-recall-interval'),
])
def test_a_resealed_summary_with_malformed_estimates_is_refused(estimated, triage, damage):
    broken = deepcopy(estimated)
    damage(broken['estimates'])
    with pytest.raises(ValueError, match='malformed'):
        gate.evaluate(reseal(broken), triage, requirement(min_triage_recall=.2))
