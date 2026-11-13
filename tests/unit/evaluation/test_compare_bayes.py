"""`ahl compare` shows a supplied bayesian_decision block, display only, and renders nothing new without one."""
import copy
import json
import re
from unittest.mock import patch

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation import bayes_decision, compare, side_by_side as sbs
from agent_harness_lab.evaluation.statistics import analyze
from agent_harness_lab.evaluation.bayes_analysis import attach
from tests.support.bayes_records import bank
from tests.unit.evaluation.test_compare_viewer import fixture, page

SECTION = 'Bayesian decision score'


def block_for(kind):
    case = bank(kind)
    return attach(case['spec'], case['records'], analyze(case['spec'], case['records']))['bayesian_decision']


def with_block(block):
    value = fixture()
    value['analysis'] = dict(value['analysis'], bayesian_decision=block)
    return value


def test_a_report_without_the_block_has_no_bayesian_section():
    assert SECTION not in page(fixture()) and 'bayes' not in page(fixture()).lower()


def test_the_block_renders_as_labelled_text_with_the_evidence_status_and_no_authority():
    document = page(with_block(block_for('clear_win')))
    assert SECTION in document
    for text in ('better', 'all_gate_stages_passed', 'recommendation-only', 'no peer-reviewed work applies this method',
                 'Copied verbatim, not recomputed here'):
        assert text.lower() in document.lower()
    assert re.search(r'automatic promotion[^<]*</td><td>false', document, re.I)
    style = re.search(r'<style>(.*?)</style>', document).group(1)
    assert not re.search(r'color|#[0-9a-f]{3,6}', style, re.I) and 'style=' not in document and '<script' not in document


def test_unknown_arms_render_the_bounds_and_an_unknown_point_score_never_zero():
    block = block_for('mixed_unknowns')
    document = page(with_block(block))
    row = re.search(r'<td>Point score S</td><td>([^<]*)</td>', document)
    assert row and row.group(1) == 'unknown'
    assert f"{block['score']['S_low']:.4f}" in document and f"{block['score']['S_high']:.4f}" in document
    assert 'unknown_outcomes' in document


def test_the_section_shows_the_gate_stages_and_the_cross_check_and_resource_state():
    block = block_for('clear_win')
    document = page(with_block(block))
    for stage in ('validity', 'critical_veto', 'quality', 'regression_controls', 'resource'):
        assert f'<td>{stage}</td>' in document
    assert '<td>not_declared</td>' in document and block['cross_check']['method'] in document


def test_a_blocked_block_with_no_score_renders_without_error():
    document = page(with_block(block_for('starter_bank') | dict(verdict='blocked', reason='oracle_quarantined', score=None,
                                                                 families=None, cross_check=dict(status='not_evaluated'))))
    assert 'oracle_quarantined' in document and SECTION in document


def test_a_hostile_or_malformed_block_is_escaped_or_skipped_never_rendered_as_markup():
    block = copy.deepcopy(block_for('clear_win'))
    block['verdict'] = '<script>alert(1)</script>'
    block['reason'] = '<img src=x onerror=alert(1)>'
    document = page(with_block(block))
    assert '<script' not in document and '<img' not in document and '&lt;script&gt;' in document
    for malformed in ('text', 7, ['a'], None, dict(verdict=['x'], score='s', gate='g', roots=3)):
        assert '<script' not in page(with_block(malformed))


def test_rendering_never_recomputes_the_block():
    value = with_block(block_for('clear_win'))
    report = sbs.build(value)
    with patch.object(bayes_decision, 'decide', side_effect=AssertionError('recomputed')), \
            patch.object(bayes_decision, '_posterior', side_effect=AssertionError('recomputed')):
        assert SECTION in compare.render(report)
    assert report['analysis']['bayesian_decision'] == value['analysis']['bayesian_decision']  # copied verbatim


def test_the_command_writes_the_section_and_keeps_the_input_unchanged(tmp_path):
    source = tmp_path / 'experiment.json'
    source.write_text(json.dumps(with_block(block_for('clear_win'))))
    before = source.read_bytes()
    assert main(['compare', '--experiment', str(source), '--output', str(tmp_path / 'review')]) == 0
    assert SECTION in (tmp_path / 'review' / 'report.html').read_text() and source.read_bytes() == before
    saved = json.loads((tmp_path / 'review' / 'report.json').read_text())
    assert saved['analysis']['bayesian_decision']['verdict'] == 'better' and saved['publication_allowed'] is False
