"""Resource decision and cost-per-success in the static review page (#124, #37); synthetic records only."""
import copy
import json
import re

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation import compare, side_by_side as sbs
from agent_harness_lab.evaluation.resource_decision import decide, success_cost
from tests.unit.evaluation.test_compare_viewer import fixture
from tests.unit.evaluation.test_resource_decision import contract, pairs, sc_arm, sc_pairs, timing_contract, timing_rows

SECTION = 'Resource decision'


def with_resource(**blocks):
    return dict(fixture(), **blocks)


def cells(document, heading):
    """Text cells of the first table after an <h3> heading."""
    table = document.split(f'<h3>{heading}', 1)[1].split('</table>', 1)[0]
    return [re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row) for row in re.findall(r'<tr>(.*?)</tr>', table)]


def favorable():
    return decide(contract(), pairs(60, 10), 'supported')


def test_supplied_decision_is_copied_verbatim_and_never_recomputed():
    decision = favorable()
    source = with_resource(resource_decision=decision)
    before = copy.deepcopy(source)
    report = sbs.build(source)
    assert (report['resource_decision'], report['resource_source']) == (decision, 'supplied_verbatim')
    assert source == before
    decision['decision'] = 'regression'
    assert report['resource_decision']['decision'] == 'improvement'


def test_decision_renders_interval_scope_horizon_and_counts_beside_the_verdict():
    document = compare.render(sbs.build(with_resource(resource_decision=favorable())))
    header, row = cells(document, SECTION)[:2]
    named = dict(zip(header, row))
    assert named['Endpoint'] == 'elapsed' and named['Scope'] == 'all_assigned_capped' and named['Status'] == 'improvement'
    assert named['Horizon'] == '100' and named['Max increase'] == '5' and named['Roots'] == '100'
    assert float(named['Mean difference']) == pytest.approx(-50)
    assert float(named['Lower']) <= float(named['Mean difference']) <= float(named['Upper']) < 0
    assert named['Capped observations'] == '0' and named['Unknown reasons'] == 'none'
    assert 'Decision: <strong>improvement</strong>' in document and 'supplied, not recomputed' in document


def test_page_and_report_without_a_resource_block_are_unchanged():
    report = sbs.build(fixture())
    assert (report['resource_decision'], report['resource_success_cost'], report['resource_source']) == (None, None, 'not_supplied')
    assert SECTION not in compare.render(report) and 'Cost per success' not in compare.render(report)


def test_fast_failure_with_unverified_quality_stays_visible_and_is_not_labelled_improvement():
    decision = decide(contract(), pairs(60, 1), 'inconclusive')
    document = compare.render(sbs.build(with_resource(resource_decision=decision)))
    assert 'Decision: <strong>quality_not_verified</strong>' in document and 'Quality status: inconclusive' in document
    assert 'Decision: <strong>improvement' not in document and 'efficiency_improvement' not in document


def test_censored_endpoint_renders_counts_and_not_computed_never_zero():
    rows = pairs(40, 10)
    for row in rows: row['candidate'] = dict(status='agent_timeout', measurements=dict(elapsed=50))  # below the horizon: censored
    decision = decide(contract(), rows, 'supported')
    named = dict(zip(*cells(compare.render(sbs.build(with_resource(resource_decision=decision))), SECTION)[:2]))
    assert named['Status'] == 'unknown' and named['Unknown reasons'] == 'censored_below_horizon=100'
    assert (named['Mean difference'], named['Lower'], named['Upper']) == ('not computed',) * 3 and named['Roots'] == 'not computed'


def test_attestation_labels_of_a_conditional_decision_are_shown_verbatim():
    decision = decide(timing_contract(), timing_rows(), 'supported')
    document = compare.render(sbs.build(with_resource(resource_decision=decision)))
    assert 'Decision: <strong>conditional_improvement</strong>' in document
    for label in ('caller_attested_unverified', 'caller_supplied_unverified', 'conditional_on_caller_inputs'): assert label in document
    assert decision['contract_sha256'] in document


def test_endpoint_text_is_escaped():
    decision = favorable()
    decision['endpoints'][0]['name'] = '<script>alert(1)</script>'
    document = compare.render(sbs.build(with_resource(resource_decision=decision)))
    assert '<script' not in document and '&lt;script&gt;alert(1)&lt;/script&gt;' in document


def broken(change):
    decision = favorable()
    change(decision)
    return decision


REFUSED = {
    'favorable-with-unverified-quality': lambda d: d.update(quality_status='inconclusive'),
    'favorable-with-missing-quality': lambda d: d.update(quality_status=None),
    'favorable-with-unknown-required-endpoint': lambda d: [d['endpoints'][0].pop(name) for name in ('mean_difference', 'low', 'high')] + [d['endpoints'][0].update(status='unknown')],
    'automatic-promotion': lambda d: d.update(automatic_promotion=True),
    'publication-allowed': lambda d: d.update(publication_allowed=True),
    'unsupported-profile': lambda d: d.update(profile='resource-noninferiority-v9'),
    'unknown-decision-label': lambda d: d.update(decision='promote'),
    'unknown-endpoint-status': lambda d: d['endpoints'][0].update(status='great'),
    'nonfinite-number': lambda d: d['endpoints'][0].update(high=float('nan')),
    'negative-capped-count': lambda d: d['endpoints'][0].update(capped_observations=-1),
    'no-endpoints': lambda d: d.update(endpoints=[]),
    'oversized-endpoints': lambda d: d.update(endpoints=d['endpoints'] * 65),
    'empty-object': lambda d: d.clear(),
}


@pytest.mark.parametrize('change', REFUSED.values(), ids=REFUSED)
def test_inconsistent_or_unauthorized_resource_decision_refuses_the_whole_input(change):
    with pytest.raises(ValueError, match='resource decision'):
        sbs.build(with_resource(resource_decision=broken(change)))


def test_unknown_endpoint_cannot_carry_numeric_effects():
    rows = pairs(40, 10)
    rows[0]['candidate']['status'] = 'infrastructure_error'
    decision = decide(contract(), rows, 'supported')
    assert decision['endpoints'][0]['status'] == 'unknown'
    decision['endpoints'][0].update(mean_difference=0, low=0, high=0)
    with pytest.raises(ValueError, match='resource decision'):
        sbs.build(with_resource(resource_decision=decision))


def test_non_object_decision_is_refused():
    with pytest.raises(ValueError, match='resource decision'):
        sbs.build(with_resource(resource_decision=['improvement']))


def views():
    rows = sc_pairs([(10, True), (10, True)], [(10, True), (90, False, 'agent_timeout')])
    return [success_cost(rows, 'elapsed'), success_cost(sc_pairs([(10, True)], [(1, False)]), 'elapsed')]


def test_cost_per_success_renders_all_assigned_beside_success_conditional_and_ratio_status():
    document = compare.render(sbs.build(with_resource(resource_success_cost=views())))
    table = cells(document, 'Cost per success')
    named = [dict(zip(table[0], row)) for row in table[1:]]
    candidate = named[1]
    assert (candidate['Arm'], candidate['All assigned mean'], candidate['Success conditional mean'], candidate['Cost per success']) == ('candidate', '50.0', '10.0', '100.0')
    assert 'descriptive' in document and 'cost ratio 10.0' in document and 'never passes a gate' in document


def test_zero_success_arm_has_no_cost_per_success_and_is_labelled_unfavorable():
    document = compare.render(sbs.build(with_resource(resource_success_cost=views())))
    table = cells(document, 'Cost per success')
    zero = dict(zip(table[0], table[-1]))
    assert (zero['Arm'], zero['Successes'], zero['Cost per success']) == ('candidate', '0', 'undefined')
    assert 'unfavorable_no_candidate_success' in document and 'cost ratio undefined' in document


def test_unknown_rows_render_unknown_means_never_zero():
    rows = [dict(root='r', baseline=sc_arm(10, True), candidate=dict(status='infrastructure_error', success=True, measurements=dict(elapsed=1)))]
    table = cells(compare.render(sbs.build(with_resource(resource_success_cost=[success_cost(rows, 'elapsed')]))), 'Cost per success')
    candidate = dict(zip(table[0], table[2]))
    assert (candidate['All assigned mean'], candidate['Success conditional mean'], candidate['Cost per success']) == ('unknown',) * 3
    assert candidate['Unknown reasons'] == 'status_infrastructure_error=1'


COST_REFUSED = {
    'gate-eligible': lambda v: v.update(gate_eligible=True),
    'passes-gate': lambda v: v.update(passes_gate=True),
    'promotion': lambda v: v.update(automatic_promotion=True),
    'wrong-profile': lambda v: v.update(profile='resource-success-cost-v2'),
    'zero-success-with-zero-cost': lambda v: v['candidate'].update(successes=0, cost_per_success=0),
    'unknown-arm-with-number': lambda v: v['candidate'].update(unknown_reasons={'missing_measurement': 1}, all_assigned_mean=5),
    'negative-mean': lambda v: v['baseline'].update(all_assigned_mean=-1),
    'nonfinite-ratio': lambda v: v.update(cost_ratio=float('inf')),
    'unknown-status': lambda v: v.update(status='cheaper'),
}


@pytest.mark.parametrize('change', COST_REFUSED.values(), ids=COST_REFUSED)
def test_inconsistent_cost_per_success_view_refuses_the_whole_input(change):
    view = views()[0]
    change(view)
    with pytest.raises(ValueError, match='cost-per-success'):
        sbs.build(with_resource(resource_success_cost=[view]))


@pytest.mark.parametrize('value', [{}, [], 'views', [[]], views() * 33], ids=['object', 'empty', 'string', 'nested', 'oversized'])
def test_cost_per_success_must_be_a_bounded_nonempty_list_of_views(value):
    with pytest.raises(ValueError, match='cost-per-success'):
        sbs.build(with_resource(resource_success_cost=value))


def test_cli_writes_the_resource_blocks_into_the_private_report(tmp_path):
    source = tmp_path / 'experiment.json'
    source.write_text(json.dumps(with_resource(resource_decision=favorable(), resource_success_cost=views())))
    assert main(['compare', '--experiment', str(source), '--output', str(tmp_path / 'review')]) == 0
    report = json.loads((tmp_path / 'review' / 'report.json').read_text())
    assert report['resource_decision']['decision'] == 'improvement' and len(report['resource_success_cost']) == 2
    assert report['publication_allowed'] is False and report['automatic_promotion'] is False
    assert SECTION in (tmp_path / 'review' / 'report.html').read_text()
