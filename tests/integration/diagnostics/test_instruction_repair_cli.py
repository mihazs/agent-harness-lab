"""`ahl instruction-repair` end to end on synthetic files: exit codes, scope, redaction and output safety."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from agent_harness_lab.diagnostics import instruction_adherence as adherence
from agent_harness_lab.diagnostics import instruction_repair as repair
from tests.support.repair_fixture import BROKEN, COMPLIANT, CONTROL, CONTROL_CLEAN, RULE, arm, document, target_ref

ROOT = Path(__file__).resolve().parents[3]
SECRET = 'zz-private-token'


def run(folder, *args):
    env = {**os.environ, 'PYTHONPATH': str(ROOT)}
    return subprocess.run([sys.executable, '-m', 'agent_harness_lab', 'instruction-repair', *args], cwd=folder, env=env,
                          capture_output=True, text=True, timeout=60)


def save(folder, name, sessions):
    (folder / name).write_text(json.dumps(document(sessions)))


@pytest.fixture
def files(tmp_path):
    roots = [f'r{n}' for n in range(5)]
    kept = ['p0', 'p1', 'p2']
    save(tmp_path, 'broken.json', arm(roots, BROKEN, prefix=f'id-{SECRET}'))
    save(tmp_path, 'fixed.json', arm(roots, COMPLIANT, prefix='c'))
    save(tmp_path, 'kept.json', arm(kept, CONTROL_CLEAN, prefix='k'))
    (tmp_path / 'AGENTS.md').write_text(RULE + CONTROL)
    (tmp_path / 'GLOBAL.md').write_text('- Never run `rm -rf /`.\n')
    sessions = adherence.load_sessions(json.loads((tmp_path / 'broken.json').read_text()))
    report = adherence.analyze(sessions, [dict(layer='project', name='AGENTS.md', text=RULE + CONTROL)])
    (tmp_path / 'report.json').write_text(json.dumps(report))
    return tmp_path


def judge(folder, *extra, preserved=True):
    args = ['judge', '--target', target_ref(), '--baseline-rules', 'project=AGENTS.md', '--candidate-rules', 'project=AGENTS.md',
            '--reproduction', 'broken.json', 'fixed.json']
    return run(folder, *args, *(['--preserved', 'kept.json', 'kept.json'] if preserved else []), *extra)


def test_plan_prints_a_redacted_recommendation_only_plan_and_a_short_summary(files):
    result = run(files, 'plan', 'report.json', '--rules', 'project=AGENTS.md')
    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert plan['profile'] == repair.PLAN_PROFILE and plan['change_authorized'] is False and len(plan['candidates']) == 5
    assert SECRET not in result.stdout and 'git push' not in result.stdout
    assert 'recommendation only' in result.stderr and len(result.stderr.splitlines()) <= 5


def test_plan_output_is_byte_identical_across_runs(files):
    first, second = (run(files, 'plan', 'report.json', '--rules', 'project=AGENTS.md').stdout for _ in range(2))
    assert first == second != ''


def test_plan_reads_global_rules_only_inside_the_global_scope(files):
    refused = run(files, 'plan', 'report.json', '--rules', 'global=GLOBAL.md')
    allowed = run(files, 'plan', 'report.json', '--rules', 'global=GLOBAL.md', '--scope', 'project+global')
    assert refused.returncode == 2 and 'layer' in refused.stderr and refused.stdout == ''
    assert allowed.returncode == 0


def test_plan_output_file_is_created_exclusively(files):
    first = run(files, 'plan', 'report.json', '--output', 'plan.json')
    again = run(files, 'plan', 'report.json', '--output', 'plan.json')
    assert (first.returncode, first.stdout) == (0, '') and json.loads((files / 'plan.json').read_text())['profile'] == repair.PLAN_PROFILE
    assert again.returncode == 2


@pytest.mark.parametrize('content', ['', '[]', '{"profile": "other"}'], ids=['empty', 'not-an-object', 'wrong-profile'])
def test_plan_refuses_a_report_of_another_shape(files, content):
    (files / 'bad.json').write_text(content)
    result = run(files, 'plan', 'bad.json')
    assert result.returncode == 2 and result.stdout == '' and 'instruction-repair' in result.stderr


def test_judge_prints_the_verdict_and_its_summary(files):
    result = judge(files)
    assert result.returncode == 0, result.stderr
    verdict = json.loads(result.stdout)
    assert verdict['verdict'] == 'supported_by_recorded_pair' and verdict['validated_improvement'] is False
    assert SECRET not in result.stdout and 'git push' not in result.stdout
    assert 'supported_by_recorded_pair' in result.stderr and 'not a validated improvement' in result.stderr


def test_judge_without_preserved_arms_is_inconclusive_not_supported(files):
    result = judge(files, preserved=False)
    assert result.returncode == 0 and json.loads(result.stdout)['reasons'] == ['preserved_not_supplied']


def test_judge_exits_zero_for_a_rejected_repair_because_the_verdict_is_the_result(files):
    (files / 'weak.md').write_text(CONTROL)
    result = run(files, 'judge', '--target', target_ref(), '--baseline-rules', 'project=AGENTS.md', '--candidate-rules', 'project=weak.md',
                 '--reproduction', 'broken.json', 'fixed.json', '--preserved', 'kept.json', 'kept.json')
    assert result.returncode == 0 and json.loads(result.stdout)['reasons'] == ['rule_removed']


def test_judge_refuses_an_unknown_target(files):
    result = run(files, 'judge', '--target', '0' * 16, '--baseline-rules', 'project=AGENTS.md', '--candidate-rules', 'project=AGENTS.md',
                 '--reproduction', 'broken.json', 'fixed.json')
    assert result.returncode == 2 and 'target' in result.stderr and result.stdout == ''


def test_judge_refuses_a_rule_file_outside_the_scope(files):
    result = judge(files, '--baseline-rules', 'global=GLOBAL.md')
    assert result.returncode == 2 and 'layer' in result.stderr


def test_judge_output_file_is_created_exclusively(files):
    assert judge(files, '--output', 'verdict.json').returncode == 0
    assert judge(files, '--output', 'verdict.json').returncode == 2
    assert json.loads((files / 'verdict.json').read_text())['profile'] == repair.VERDICT_PROFILE


def test_judge_refuses_a_missing_session_file(files):
    result = run(files, 'judge', '--target', target_ref(), '--baseline-rules', 'project=AGENTS.md', '--candidate-rules', 'project=AGENTS.md',
                 '--reproduction', 'broken.json', 'absent.json')
    assert result.returncode == 2 and result.stdout == ''


def test_help_states_the_boundary(files):
    result = run(files, '--help')
    assert result.returncode == 0 and 'recommendation' in result.stdout.lower() and 'offline' in result.stdout.lower()
