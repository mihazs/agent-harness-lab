"""A recorded compaction travels from a native capture through `ahl instruction-adherence` into `ahl instruction-repair plan` (part of #656)."""
import json
import os
from pathlib import Path
import subprocess
import sys

from agent_harness_lab.diagnostics import instruction_adherence as adherence
from tests.support.adherence_fixture import SOURCE, agent, session, user
from tests.support.native_session_fixture import claude, write
from tests.support.repair_fixture import RULE, VIOLATION

ROOT = Path(__file__).resolve().parents[3]


def ahl(folder, command, *args):
    env = {**os.environ, 'PYTHONPATH': str(ROOT)}
    return subprocess.run([sys.executable, '-m', 'agent_harness_lab', command, *args], cwd=folder, env=env, capture_output=True, text=True, timeout=60)


def plan_from(folder, report):
    (folder / 'report.json').write_text(json.dumps(report))
    result = ahl(folder, 'instruction-repair', 'plan', 'report.json', '--rules', 'project=AGENTS.md')
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)['candidates'][0]['hypotheses']


def test_a_sessions_file_with_a_compaction_before_the_violation_yields_a_context_loss_candidate_that_names_it(tmp_path):
    turns = [user('Publish the change.'), *[agent('Working.')] * 4, VIOLATION]
    document = dict(profile=adherence.SESSIONS_PROFILE, sessions=[session('s1', turns, loaded={SOURCE: 'loaded'}, root='r1', compactions=[3])])
    (tmp_path / 'sessions.json').write_text(json.dumps(document))
    (tmp_path / 'AGENTS.md').write_text(RULE)
    reported = ahl(tmp_path, 'instruction-adherence', 'sessions.json', '--rules', 'project=AGENTS.md')
    assert reported.returncode == 0, reported.stderr
    report = json.loads(reported.stdout)
    assert [row['compaction'] for row in report['findings']] == ['in_window']
    context = [row for row in plan_from(tmp_path, report) if row['mechanism'] == 'context_loss']
    assert [(row['basis'], row['support'], row['status']) for row in context] == [('not_excluded', ['compaction_in_window'], 'hypothesis')]


def test_a_native_claude_capture_with_a_compaction_reports_it_in_the_window(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'AGENTS.md').write_text(RULE)
    rows = claude(project, [('memory', str(project / 'AGENTS.md'), RULE), ('user', 'Publish.'), ('compact',), ('summary',),
                            ('call', 'Bash', dict(command='git push --force origin main'))])
    capture = write(tmp_path / 'capture.jsonl', rows)
    reported = ahl(project, 'instruction-adherence', '--native', 'claude', '--project', str(project), str(capture), '--rules', 'project=AGENTS.md')
    assert reported.returncode == 0, reported.stderr
    assert [(row['status'], row['compaction']) for row in json.loads(reported.stdout)['findings']] == [('observed_violation', 'in_window')]


def test_a_sessions_file_with_compaction_positions_outside_the_session_is_refused_with_exit_2(tmp_path):
    document = dict(profile=adherence.SESSIONS_PROFILE, sessions=[session('s1', [user('Hi.')], compactions=[5])])
    (tmp_path / 'sessions.json').write_text(json.dumps(document))
    result = ahl(tmp_path, 'instruction-adherence', 'sessions.json')
    assert (result.returncode, result.stdout) == (2, '') and 'compactions' in result.stderr
