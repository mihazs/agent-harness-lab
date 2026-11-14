"""The six synthetic Bayesian fixtures, built by the documented script and judged by the documented command.

The expected verdicts are committed in examples/bayes-decision/expected-verdicts.json and follow from how each bank is
built, so a fixture cannot be tuned to its number without a visible change to both. Everything is offline, seeded and synthetic.
"""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tests.support.bayes_records import KINDS, expected_verdicts, run
from tests.support.bayes_scenarios import SHAPES

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / 'scripts' / 'build_bayes_fixtures.py'
VERDICTS = ('better', 'equivalent', 'worse', 'insufficient_evidence', 'blocked')
AUTHORITY = ('recommendation_authority', 'automatic_promotion', 'publication_allowed')


def build(out, *kinds):
    return subprocess.run([sys.executable, str(SCRIPT), *kinds, '--out', str(out)], capture_output=True, text=True,
                          timeout=120, cwd=out)


def judge(directory):
    """Run the documented command on a built fixture; return (exit code, raw stdout, block)."""
    code, out, err = run('experiment', '--spec', str(directory / 'plan.json'), '--records', str(directory / 'trials.json'))
    assert err == ''
    return code, out, json.loads(out)['bayesian_decision']


def built(tmp_path, kind):
    result = build(tmp_path, kind)
    assert result.returncode == 0, result.stderr
    return tmp_path / kind


def test_the_committed_expectations_name_exactly_the_six_fixtures():
    rows = expected_verdicts()
    assert tuple(sorted(rows)) == tuple(sorted(KINDS))
    for kind, row in rows.items():
        assert row['verdict'] in VERDICTS and all(type(row[key]) is str and row[key] for key in ('reason', 'stage', 'analysis')), kind


@pytest.mark.parametrize('kind', KINDS)
def test_the_documented_script_and_command_reach_the_committed_verdict(tmp_path, kind):
    code, out, block = judge(built(tmp_path, kind))
    row = expected_verdicts()[kind]
    assert code == 3  # a recorded descriptive analysis always exits 3
    assert (block['verdict'], block['reason'], block['stage']) == (row['verdict'], row['reason'], row['stage'])
    assert all(block[name] is False for name in AUTHORITY)
    result = json.loads(out)
    assert result['status'] == row['analysis']  # the fixed Hoeffding analysis beside the block keeps its own answer
    assert result['promotion']['automatic'] is False and result['promotion']['status'] != 'recommended'


@pytest.mark.parametrize('kind', KINDS)
def test_a_rebuilt_fixture_and_a_second_judgement_are_byte_identical(tmp_path, kind):
    first, second = tmp_path / 'one', tmp_path / 'two'
    for directory in (first, second):
        directory.mkdir()
    assert build(first, kind).returncode == 0 and build(second, kind).returncode == 0
    for name in ('plan.json', 'trials.json'):
        assert (first / kind / name).read_bytes() == (second / kind / name).read_bytes()
    assert judge(first / kind)[1] == judge(second / kind)[1]


def test_the_build_command_writes_all_six_fixtures_at_once(tmp_path):
    assert build(tmp_path, 'all').returncode == 0
    assert sorted(path.name for path in tmp_path.iterdir()) == sorted(KINDS)
    assert all((tmp_path / kind / name).is_file() for kind in KINDS for name in ('plan.json', 'trials.json'))


def test_the_build_command_refuses_to_overwrite_a_fixture_or_to_build_an_unknown_one(tmp_path):
    assert build(tmp_path, 'tie').returncode == 0
    before = (tmp_path / 'tie' / 'plan.json').read_bytes()
    again = build(tmp_path, 'tie')
    assert again.returncode == 2 and 'exists' in again.stderr and (tmp_path / 'tie' / 'plan.json').read_bytes() == before
    unknown = build(tmp_path, 'not_a_fixture')
    assert unknown.returncode == 2 and not (tmp_path / 'not_a_fixture').exists()


def test_the_mixed_bank_bounds_its_ungraded_arms_and_never_counts_them_as_failures(tmp_path):
    block = judge(built(tmp_path, 'mixed_unknowns'))[2]
    score = block['score']
    assert block['unknown_arms'] > 0 and score['estimate'] is None and score['S_worse'] is None and score['S_equiv'] is None
    assert score['S_low'] < score['S_high'] and block['verdict'] != 'blocked'
    # the ungraded arms are a win-shaped bank's missing outcomes: the best completion wins, so no completion is a loss
    assert score['S_high'] >= block['parameters']['p_cut'] and score['S_worse_high'] == 0
    assert block['bound_resolution'] == 'unresolved' and block['critical_regression'] == 'unknown'


def test_the_veto_bank_reports_the_veto_before_any_score_is_read(tmp_path):
    block = judge(built(tmp_path, 'critical_veto'))[2]
    assert block['critical_regression'] == 'observed' and block['score'] is None and block['families'] is None
    assert [(g['stage'], g['status']) for g in block['gate']] == [
        ('validity', 'passed'), ('critical_veto', 'failed'), ('quality', 'not_evaluated'),
        ('regression_controls', 'not_evaluated'), ('resource', 'not_evaluated')]


def test_the_starter_bank_is_withheld_by_the_root_floor_although_its_posterior_alone_would_pass(tmp_path):
    block = judge(built(tmp_path, 'starter_bank'))[2]
    assert block['roots'] == dict(selected=6, independent=6) and block['floors']['roots']['met'] is False
    assert block['score']['estimate'] >= block['parameters']['p_cut']  # the score is still reported
    # six roots cannot clear the calibrated cross-check level either (alpha .01): their smallest exact p-value is 2 ** -6
    assert block['cross_check']['p_better'] == 2 ** -6 > block['parameters']['cross_check_alpha']
    assert block['verdict'] == 'insufficient_evidence'


def test_the_generator_keeps_the_construction_the_committed_verdicts_follow_from():
    # (roots per family, P(baseline passes), P(candidate rescues a baseline failure), P(candidate breaks a baseline pass))
    assert SHAPES == {'clear_win': (30, .4, .7, .05), 'clear_loss': (30, .6, .05, .7), 'tie': (60, .5, .05, .05)}
    effect = {kind: round((1 - base) * rescue + base * (1 - broken) - base, 10) for kind, (_, base, rescue, broken) in SHAPES.items()}
    assert effect == {'clear_win': .4, 'clear_loss': -.4, 'tie': 0}  # the true paired differences the reference page states


def test_the_win_and_loss_banks_are_mirrors_and_the_tie_is_not_decided_by_direction(tmp_path):
    win, loss, tie = (judge(built(tmp_path, kind))[2]['score'] for kind in ('clear_win', 'clear_loss', 'tie'))
    assert win['estimate'] >= .95 and loss['S_worse'] >= .95 and tie['S_equiv'] >= .95
    assert loss['estimate'] == 0 and win['S_worse'] == 0 and tie['estimate'] == tie['S_worse'] == 0
