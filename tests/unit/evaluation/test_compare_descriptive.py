"""`ahl compare` shows a supplied descriptive_report, display only, and renders nothing new without one."""
import copy
import re
from unittest.mock import patch

from agent_harness_lab.evaluation import bayes_descriptive, compare, side_by_side as sbs
from agent_harness_lab.evaluation.bayes_analysis import attach
from agent_harness_lab.evaluation.statistics import analyze
from tests.support.bayes_records import bank
from tests.support.bayes_scenarios import plan
from tests.unit.evaluation.test_compare_viewer import fixture, page

SECTION = 'Descriptive report'


def report_for(kind):
    case = bank(kind)
    return attach(case['spec'], case['records'], analyze(case['spec'], case['records']))['descriptive_report']


def with_report(value):
    result = fixture()
    result['analysis'] = dict(result['analysis'], descriptive_report=value)
    return result


def test_a_report_without_the_descriptive_report_has_no_descriptive_section():
    assert SECTION not in page(fixture()) and 'Pareto' not in page(fixture())


def test_the_report_renders_labelled_descriptive_only_and_never_as_a_verdict():
    document = page(with_report(report_for('clear_win')))
    assert SECTION in document
    for text in ('descriptive only', 'not a verdict', 'candidate_dominates', 'Pareto', 'not recomputed here'):
        assert text.lower() in document.lower()
    assert re.search(r'passes gate[^<]*</td><td>false', document, re.I)
    style = re.search(r'<style>(.*?)</style>', document).group(1)
    assert not re.search(r'color|#[0-9a-f]{3,6}', style, re.I) and 'style=' not in document and '<script' not in document


def test_a_bounded_bank_renders_its_bounds_and_an_unknown_point_never_zero():
    analysis = dict(status='incomplete', missing_outcome_bounds=dict(primary=dict(identified_low=-.1, identified_high=.3)))
    report = bayes_descriptive.report(plan(5), analysis)
    document = page(with_report(report))
    row = re.search(r'<td>Desirability summary</td><td>([^<]*)</td>', document)
    assert row and row.group(1) == 'unknown'
    assert f"{report['desirability']['low']:.4f}" in document and f"{report['desirability']['high']:.4f}" in document


def test_rendering_never_recomputes_the_report():
    value = with_report(report_for('clear_win'))
    built = sbs.build(value)
    with patch.object(bayes_descriptive, 'report', side_effect=AssertionError('recomputed')), \
            patch.object(bayes_descriptive, 'epsilon_indicator', side_effect=AssertionError('recomputed')):
        assert SECTION in compare.render(built)
    assert built['analysis']['descriptive_report'] == value['analysis']['descriptive_report']  # copied verbatim


def test_a_hostile_or_malformed_report_is_escaped_or_skipped_never_rendered_as_markup():
    report = copy.deepcopy(report_for('clear_win'))
    report['relation'] = '<script>alert(1)</script>'
    report['objectives'][0]['name'] = '<img src=x onerror=alert(1)>'
    document = page(with_report(report))
    assert '<script' not in document and '<img' not in document and '&lt;script&gt;' in document
    for malformed in ('text', 7, ['a'], None, dict(relation=['x'], objectives='o', desirability=3, epsilon_indicator='e')):
        assert '<script' not in page(with_report(malformed))
