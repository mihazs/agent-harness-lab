"""`ahl compare` shows a supplied preservation block (#37 with #122), display only, and renders nothing new without one."""
import re
from unittest.mock import patch

import pytest

from agent_harness_lab.evaluation import compare, preservation_analysis, side_by_side as sbs
from agent_harness_lab.evaluation.statistics import analyze
from tests.unit.evaluation import test_preservation_analysis as math
from tests.unit.evaluation.test_compare_public import public_fixture
from tests.unit.evaluation.test_compare_viewer import fixture, page
from tests.unit.evaluation.test_compare_viewer_audit import HOSTILE, audit

SECTION = 'Preservation cohorts'


def analysis_for(outcome, *, drop=0, roots=6, members=4):
    """A real `statistics.analyze` result on synthetic roots, the first `members` frozen as one cohort; `drop` removes leading trial records."""
    spec = math.plan(n=roots, repeats=2)
    spec, frozen = math.freeze(spec, members=[task[0] for task in spec.tasks[:members]])
    return analyze(spec, math.records(spec, outcome)[drop:], cohort_binding=frozen)


def repaired(row):
    return row['version'] == 'candidate' or int(row['task'][1:]) < 2


def aggregate_gain_with_protected_loss(row):
    return (row['version'] == 'baseline') == (int(row['task'][1:]) < 3)  # three protected roots lose, seven others gain


def shown(analysis):
    return dict(fixture(), analysis=analysis)


def rows(document, heading):
    """The rows of the first table after `heading`, each as a header-to-cell dict."""
    section = document.split(f'{heading}</h3>')[1].split('</table>')[0]
    headers = re.findall(r'<th scope="col">([^<]*)</th>', section)
    return [dict(zip(headers, re.findall(r'<td>([^<]*)</td>', row))) for row in re.findall(r'<tr>(.*?)</tr>', section.split('<tbody>')[1])]


def measures(document, heading):
    return {row['Measure']: row['Value'] for row in rows(document, heading)}


def test_a_report_without_the_block_has_no_preservation_section():
    assert SECTION not in page(fixture()) and 'preservation' not in page(fixture()).lower()


def test_the_block_renders_the_paired_transition_cells_and_the_authority_limits_as_text():
    result = analysis_for(repaired)
    document = page(shown(result))
    assert SECTION in document
    overall = measures(document, 'Paired transitions, all frozen pairs')
    transitions = result['preservation']['descriptive']['overall']['transitions']
    assert overall['Baseline fail, candidate pass'] == str(transitions['fail_pass']) == '8'
    assert overall['Baseline pass, candidate pass'] == str(transitions['pass_pass'])
    assert (overall['Baseline pass, candidate fail'], overall['Baseline fail, candidate fail']) == ('0', '0')
    assert (overall['Missing'], overall['Invalid'], overall['Unknown']) == ('0', '0', '0')
    for text in ('not recomputed here', 'not_validated_by_conditional_math', 'unverified', 'Bonferroni', 'historical_pass'):
        assert text in document


def test_a_protected_loss_hidden_by_an_aggregate_gain_stays_visible_in_its_cohort():
    result = analysis_for(aggregate_gain_with_protected_loss, roots=10, members=3)
    cohort = result['preservation']['cohorts']['protected']
    assert result['primary']['mean'] > 0 and cohort['descriptive']['transitions']['pass_fail'] == 6
    document = page(shown(result))
    (frozen,) = rows(document, 'Frozen cohorts')
    assert frozen['Mean difference'] == f"{cohort['endpoint']['mean']:.4f}" and float(frozen['Mean difference']) < 0
    assert frozen['Lower'] == '-1.0000' and cohort['endpoint']['low'] == -1  # the analysis clips at -1 as an integer
    (transitions,) = rows(document, 'Cohort transitions')
    assert transitions['Baseline pass, candidate fail'] == '6' and transitions['Baseline fail, candidate pass'] == '0'


def test_a_missing_trial_shows_unknown_interval_cells_never_zero_and_the_possible_bounds():
    result = analysis_for(repaired, drop=1)
    assert result['status'] == 'incomplete' and result['preservation']['cohorts']['protected']['endpoint'] is None
    document = page(shown(result))
    (frozen,) = rows(document, 'Frozen cohorts')
    assert [frozen[name] for name in ('Disposition', 'Mean difference', 'Lower', 'Upper', 'Independent roots')] == ['incomplete'] + ['unknown'] * 4
    assert rows(document, 'Cohort transitions')[0]['Missing'] == '1'
    (bound,) = rows(document, 'Possible cohort differences with missing trials, not imputed')
    expected = result['missing_outcome_bounds']['required_cohorts']['protected']
    assert (bound['Identified low'], bound['Pessimistic low']) == (f"{expected['identified_low']:.4f}", f"{float(expected['pessimistic_low']):.4f}")
    assert measures(document, 'Coverage of the frozen trials')['Missing trials'] == '1'


def test_rendering_never_recomputes_the_analysis():
    value = shown(analysis_for(repaired))
    built = sbs.build(value)
    with patch.object(preservation_analysis, 'analyze', side_effect=AssertionError('recomputed')), \
            patch.object(preservation_analysis, '_group', side_effect=AssertionError('recomputed')):
        assert SECTION in compare.render(built)
    assert built['analysis']['preservation'] == value['analysis']['preservation']  # copied verbatim


def test_the_section_has_no_color_script_or_audit_problem():
    document = page(shown(analysis_for(repaired)))
    style = re.search(r'<style>(.*?)</style>', document).group(1)
    assert not re.search(r'color|#[0-9a-f]{3,6}', style, re.I) and 'style=' not in document
    assert audit(document)[0] == []


def test_every_transition_the_analysis_can_report_has_a_label():
    assert tuple(key for key, _ in compare.CELLS) == preservation_analysis.TRANSITIONS


@pytest.mark.parametrize('payload', HOSTILE, ids=['tag-breakout', 'img-handler', 'attribute-breakout', 'javascript-url', 'style-breakout', 'link-injection'])
def test_hostile_text_in_the_block_stays_text(payload):
    result = analysis_for(repaired)
    block = result['preservation']
    block['cohorts'][payload] = dict(block['cohorts'].pop('protected'), role=payload, exposure=payload, disposition=payload)
    block.update(assumptions=[payload], qualification_authority=payload)
    problems, text = audit(page(shown(result)))
    assert problems == [] and payload in text


@pytest.mark.parametrize('malformed', [
    pytest.param('text', id='not-an-object'),
    pytest.param(7, id='number'),
    pytest.param(['a'], id='list'),
    pytest.param(dict(cohorts=['a'], descriptive='d', assumptions='a', error_allocation=3), id='wrong-member-types'),
    pytest.param(dict(cohorts=dict(c='x', d=dict(endpoint='e', descriptive=4))), id='cohort-not-an-object'),
    pytest.param(dict(), id='empty'),
])
def test_a_malformed_block_is_unknown_or_skipped_never_raised_or_zero(malformed):
    document = page(dict(fixture(), analysis=dict(status='inconclusive', preservation=malformed)))
    assert audit(document)[0] == []
    assert not re.search(r'<td>(Missing|Invalid|Unknown)</td><td>\d', document)


def test_a_public_export_refuses_an_analysis_carrying_the_block():
    value = dict(public_fixture(), analysis=analysis_for(repaired))
    with pytest.raises(ValueError, match='private'):
        sbs.build(value, public=True)
