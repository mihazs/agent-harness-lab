"""`ahl experiment` prints the descriptive report beside the Bayesian block and it never changes a decision.

Offline and synthetic: the six fixture banks of tests/support/bayes_records.py, run through the documented command. The
expected dominance relations are committed in examples/bayes-decision/expected-verdicts.json.
"""
import json

import pytest

from tests.support.bayes_records import KINDS, bank, expected_verdicts, experiment

AUTHORITY = ('recommendation_authority', 'automatic_promotion', 'publication_allowed', 'gate_eligible', 'passes_gate')


def run_kind(tmp_path, kind, **options):
    case = bank(kind, **options)
    code, out, err = experiment(tmp_path, case['spec'], case['records'])
    assert (code, err) == (3, '')
    return json.loads(out)


@pytest.mark.parametrize('kind', KINDS)
def test_each_fixture_renders_a_labelled_report_with_its_committed_relation(tmp_path, kind):
    result = run_kind(tmp_path, kind)
    report = result['descriptive_report']
    assert report['relation'] == expected_verdicts()[kind]['descriptive_relation']
    assert report['descriptive_only'] is True and report['decision_input'] is False
    assert all(report[name] is False for name in AUTHORITY)
    assert report['resources'] == 'not_declared' and [o['name'] for o in report['objectives']] == ['quality']
    assert list(result)[-2:] == ['bayesian_decision', 'descriptive_report']


def test_the_report_leaves_the_decision_the_fixed_analysis_and_the_promotion_unchanged(tmp_path):
    for kind in KINDS:
        directory = tmp_path / kind
        directory.mkdir()
        result = run_kind(directory, kind)
        stripped = {key: value for key, value in result.items() if key != 'descriptive_report'}
        row = expected_verdicts()[kind]
        block = result['bayesian_decision']
        assert (block['verdict'], block['reason'], block['stage']) == (row['verdict'], row['reason'], row['stage'])
        assert result['status'] == row['analysis'] and stripped['promotion'] == result['promotion']
        assert result['promotion']['automatic'] is False and result['promotion']['status'] != 'recommended'


def test_the_mixed_bank_reports_bounds_for_its_ungraded_arms_never_a_point_difference(tmp_path):
    report = run_kind(tmp_path, 'mixed_unknowns')['descriptive_report']
    quality = report['objectives'][0]
    assert quality['status'] == 'bounded' and quality['gain']['low'] < quality['gain']['high']
    # even the worst completion of the ungraded arms is past the rope, so the summary is the same (1) for every completion
    assert report['desirability']['low'] == report['desirability']['high'] == report['desirability']['value'] == 1


def test_a_plan_that_declares_no_bayes_decision_prints_no_descriptive_report(tmp_path):
    result = run_kind(tmp_path, 'clear_win', bayes=False)
    assert 'descriptive_report' not in result and 'bayesian_decision' not in result


def test_the_same_records_print_byte_identical_reports(tmp_path):
    first, second = tmp_path / 'one', tmp_path / 'two'
    for directory in (first, second):
        directory.mkdir()
    one, two = (run_kind(directory, 'tie')['descriptive_report'] for directory in (first, second))
    assert json.dumps(one, sort_keys=True) == json.dumps(two, sort_keys=True)
