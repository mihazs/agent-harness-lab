"""`ahl compare` shows a supplied precision-feasibility preflight (#37 with #9), display only; synthetic plans only."""
import copy
import json
import re
from unittest.mock import patch

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation import compare, precision, side_by_side as sbs
from tests.unit.evaluation.test_compare_public import public_fixture
from tests.unit.evaluation.test_compare_viewer import fixture, page
from tests.unit.evaluation.test_compare_viewer_audit import HOSTILE, audit
from tests.unit.evaluation.test_precision_solver import spec

SECTION = 'Precision-feasibility preflight (supplied, not recomputed here)'


def preflight(sizes=None, target=None, **options):
    """A real `precision.preflight` result on a synthetic plan: 60 declared roots in two families unless `sizes` says otherwise."""
    return precision.preflight(spec(sizes or {'a': 30, 'b': 30}, target=target), **options)


def shown(result):
    return dict(fixture(), precision_preflight=result)


def rows(document, heading):
    """The rows of the first table after the `<h3>` named `heading`, each as a header-to-cell dict."""
    section = document.split(f'{heading}</h3>')[1].split('</table>')[0]
    headers = re.findall(r'<th scope="col">([^<]*)</th>', section)
    return [dict(zip(headers, re.findall(r'<td>([^<]*)</td>', row))) for row in re.findall(r'<tr>(.*?)</tr>', section.split('<tbody>')[1])]


def measures(document, heading=SECTION):
    return {row['Measure']: row['Value'] for row in rows(document, heading)}


def test_a_report_without_the_block_has_no_precision_section_and_says_not_supplied():
    report = sbs.build(fixture())
    assert (report['precision_preflight'], report['precision_source']) == (None, 'not_supplied')
    assert 'Precision-feasibility' not in compare.render(report)


def test_the_supplied_preflight_is_copied_verbatim_and_never_recomputed():
    result = preflight()
    source = shown(result)
    before = copy.deepcopy(source)
    report = sbs.build(source)
    with patch.object(precision, 'preflight', side_effect=AssertionError('recomputed')), \
            patch.object(precision, 'measure', side_effect=AssertionError('recomputed')):
        assert SECTION in compare.render(report)
    assert (report['precision_preflight'], report['precision_source']) == (result, 'supplied_verbatim')
    assert source == before
    result['status'] = 'infeasible'
    assert report['precision_preflight']['status'] == 'feasible'


def test_distinct_task_root_and_repeat_counts_are_shown_apart_from_planned_trials():
    result = precision.preflight(spec({'a': 30, 'b': 30}, repeats=3, extra_tasks=(('a-extra', 'a-r0', 'a'),)))
    named = measures(page(shown(result)))
    assert (named['Selected tasks'], named['Distinct declared roots'], named['Repetitions per task'], named['Planned trials']) == ('61', '60', '3', '366')
    assert (named['Status'], named['Execution allowed'], named['Why not feasible']) == ('feasible', 'true', 'none')


def test_an_infeasible_plan_names_each_stated_reason_and_the_target_it_missed():
    result = precision.preflight(spec({'a': 30, 'b': 30}, target=.3))
    assert result['status'] == 'infeasible' and result['precision_target_met'] is False
    named = measures(page(shown(result)))
    assert named['Status'] == 'infeasible' and named['Execution allowed'] == 'false'
    assert named['Half-width target met'] == 'false' and named['Half-width target'] == '0.3000'
    assert 'half-width target not met' in named['Why not feasible']


def test_criteria_that_cannot_be_met_are_named_per_endpoint_while_execution_may_still_be_allowed():
    result = preflight({'a': 4, 'b': 4})
    assert (result['status'], result['execution_allowed']) == ('infeasible', True)  # no frozen target: the plan is only unlikely to succeed
    document = page(shown(result))
    reason = measures(document)['Why not feasible']
    assert all(f'{name}: the frozen criteria are not possible' in reason for name in ('primary', 'family:a', 'family:b'))
    by_name = {row['Endpoint']: row for row in rows(document, 'Precision by endpoint')}
    assert by_name['primary']['Criteria possible'] == 'false' and by_name['primary']['Half-width'] == f"{result['endpoints']['primary']['half_width']:.4f}"
    assert by_name['family:a']['Declared roots'] == '4' and by_name['family:a']['Best-case lower bound'].startswith('-')


def test_a_trial_ceiling_below_the_plan_is_a_reason_and_the_solver_reasons_are_copied():
    result = preflight(trial_limit=10)
    assert result['trial_ceiling_met'] is False
    named = measures(page(shown(result)))
    assert 'planned trials exceed the trial ceiling' in named['Why not feasible']
    assert 'sample size plan: trial_ceiling_below_minimum_trials' in named['Why not feasible']
    assert (named['Trial ceiling met'], named['Caller trial limit'], named['Planned trials']) == ('false', '10', '120')


def test_an_unknown_value_is_not_computed_or_not_checked_never_zero():
    result = preflight()
    result['status'] = 'infeasible'
    result['endpoints']['primary'].update(half_width=None, minimum_observed_effect=None, best_case_lower=None, criteria_possible=False,
                                          numeric_status='unrepresentable_by_frozen_method')
    document = page(shown(result))
    primary = next(row for row in rows(document, 'Precision by endpoint') if row['Endpoint'] == 'primary')
    assert [primary[name] for name in ('Half-width', 'Minimum observed effect', 'Best-case lower bound')] == ['not computed'] * 3
    assert primary['Numeric status'] == 'unrepresentable_by_frozen_method'
    named = measures(document)
    assert named['Effective roots after similarity grouping'] == 'not checked' and named['Caller trial limit'] == 'not set'
    assert not {'0', '0.0000'} & set(primary.values())


def test_the_authority_limits_and_roots_assumptions_are_shown_as_supplied():
    document = page(shown(preflight()))
    named = measures(document)
    assert named['Qualification authority'] == 'not_validated_by_analytic_precision' and named['Root independence'] == 'unverified'
    assert (named['Power'], named['Automatic promotion'], named['Publication allowed']) == ('not_estimated', 'false', 'false')
    assert (named['Error allocation'], named['Total alpha'], named['Endpoints sharing alpha'], named['Required cohorts']) == ('Bonferroni', '0.0500', '3', '0')
    assert 'Feasible precision is not observed improvement, noninferiority or a promotion decision.' in document
    assert 'weighted_root_Hoeffding' in document and 'not power' in document


def test_the_sample_size_plan_is_shown_as_descriptive_and_an_unavailable_plan_states_its_reason():
    result = preflight(target=.3)
    plan = result['sample_size_plan']
    named = measures(page(shown(result)), 'Sample size plan (descriptive, never authority)')
    assert (named['Status'], named['Reasons'], named['Roots required at the frozen target']) == ('feasible', 'none', str(plan['required_roots']))
    assert named['Minimum planned trials'] == str(plan['minimum_planned_trials'])
    result['sample_size_plan'] = dict(status='unavailable', reason='root_similarity_groups_require_new_frozen_evidence')
    named = measures(page(shown(result)), 'Sample size plan (descriptive, never authority)')
    assert (named['Status'], named['Reasons'], named['Minimum planned trials']) == ('unavailable', 'root_similarity_groups_require_new_frozen_evidence', 'not computed')


def test_the_section_has_no_color_script_or_audit_problem():
    document = page(shown(preflight(target=.3)))
    style = re.search(r'<style>(.*?)</style>', document).group(1)
    assert not re.search(r'color|#[0-9a-f]{3,6}', style, re.I) and 'style=' not in document
    assert audit(document)[0] == []


@pytest.mark.parametrize('payload', HOSTILE, ids=['tag-breakout', 'img-handler', 'attribute-breakout', 'javascript-url', 'style-breakout', 'link-injection'])
def test_hostile_text_in_the_block_stays_text(payload):
    result = preflight()
    result['endpoints'][payload] = dict(result['endpoints'].pop('family:a'))
    result['limitations'] = [payload]
    result['sample_size_plan'].update(status=payload, reasons=[payload])
    result['family_weights'][0]['family'] = payload
    problems, text = audit(page(shown(result)))
    assert problems == [] and payload in text


def mutate(**changes):
    def apply(result):
        result.update(changes)
    return apply


def endpoint(name, **changes):
    return lambda result: result['endpoints'][name].update(changes)


def feasible_but(**changes):
    return lambda result: result.update(status='feasible', **changes)


REFUSED = [
    pytest.param(mutate(profile='other'), id='wrong-profile'),
    pytest.param(mutate(plan_sha256='x'), id='malformed-plan-hash'),
    pytest.param(mutate(status='maybe'), id='unknown-status'),
    pytest.param(mutate(planned_trials=-1), id='negative-count'),
    pytest.param(mutate(selected_tasks=True), id='bool-as-count'),
    pytest.param(mutate(declared_roots=1.5), id='fractional-count'),
    pytest.param(mutate(precision_half_width=float('inf')), id='non-finite-target'),
    pytest.param(mutate(endpoints={}), id='no-endpoints'),
    pytest.param(mutate(endpoints=[]), id='endpoints-not-an-object'),
    pytest.param(endpoint('primary', required='yes'), id='required-not-a-bool'),
    pytest.param(endpoint('primary', criteria_possible=None), id='required-endpoint-without-a-criteria-answer'),
    pytest.param(endpoint('primary', half_width='wide'), id='half-width-not-a-number'),
    pytest.param(mutate(automatic_promotion=True), id='promotion-authority'),
    pytest.param(mutate(automatic_budget_change=True), id='budget-authority'),
    pytest.param(mutate(publication_allowed=True), id='publication-authority'),
    pytest.param(lambda result: result['sample_size_plan'].update(plan_mutation=True), id='solver-plan-mutation'),
    pytest.param(feasible_but(trial_ceiling_met=False, execution_allowed=False), id='feasible-with-the-trial-ceiling-unmet'),
    pytest.param(feasible_but(precision_target_met=False), id='feasible-with-the-target-unmet'),
    pytest.param(lambda result: (result.update(status='feasible'), result['endpoints']['family:a'].update(criteria_possible=False)), id='feasible-with-impossible-criteria'),
    pytest.param(mutate(status='infeasible', trial_ceiling_met=False, execution_allowed=True), id='execution-allowed-over-the-ceiling'),
]


@pytest.mark.parametrize('damage', REFUSED)
def test_a_malformed_or_self_contradicting_preflight_refuses_the_whole_input(damage):
    result = preflight()
    damage(result)
    with pytest.raises(ValueError, match='precision'):
        sbs.build(shown(result))


@pytest.mark.parametrize('damage, message', [
    pytest.param(feasible_but(trial_ceiling_met=False, execution_allowed=False), 'feasible precision preflight contradicts', id='feasible-guard-alone'),
    pytest.param(mutate(status='infeasible', trial_ceiling_met=False, execution_allowed=True), 'allows execution over its trial ceiling', id='execution-guard-alone'),
])
def test_each_trial_ceiling_guard_refuses_on_its_own_message(damage, message):
    result = preflight()
    damage(result)
    with pytest.raises(ValueError, match=message):
        sbs.build(shown(result))


def test_a_preflight_that_is_not_an_object_refuses_the_input():
    with pytest.raises(ValueError, match='precision'):
        sbs.build(shown(['feasible']))


def test_a_public_export_refuses_a_preflight_because_planning_material_is_private():
    with pytest.raises(ValueError, match='private'):
        sbs.build(dict(public_fixture(), precision_preflight=preflight()), public=True)


def test_the_cli_writes_the_preflight_into_the_page_and_the_report_json(tmp_path, capsys):
    source = tmp_path / 'experiment.json'
    source.write_text(json.dumps(shown(preflight())))
    assert main(['compare', '--experiment', str(source), '--output', str(tmp_path / 'review')]) == 0
    capsys.readouterr()
    assert SECTION in (tmp_path / 'review' / 'report.html').read_text()
    assert json.loads((tmp_path / 'review' / 'report.json').read_text())['precision_source'] == 'supplied_verbatim'
