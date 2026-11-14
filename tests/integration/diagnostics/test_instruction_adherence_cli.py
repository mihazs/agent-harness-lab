"""`ahl instruction-adherence` end to end on synthetic files: scope, redaction, exit codes and output safety."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from agent_harness_lab.diagnostics import instruction_adherence as adherence
from tests.support.adherence_fixture import edit, session, shell, user

ROOT = Path(__file__).resolve().parents[3]
SECRET = 'zz-private-token'


def run(folder, *args):
    env = {**os.environ, 'PYTHONPATH': str(ROOT)}
    return subprocess.run([sys.executable, '-m', 'agent_harness_lab', 'instruction-adherence', *args], cwd=folder, env=env,
                          capture_output=True, text=True, timeout=60)


@pytest.fixture
def files(tmp_path):
    turns = [user(f'Do not edit `notes/{SECRET}.md`.'), edit(f'notes/{SECRET}.md'), shell('git push --force')]
    document = dict(profile=adherence.SESSIONS_PROFILE, sessions=[session(f'id-{SECRET}', turns, loaded={'project:AGENTS.md': 'loaded'}, root='r1')])
    (tmp_path / 'sessions.json').write_text(json.dumps(document))
    (tmp_path / 'AGENTS.md').write_text('- Never run `git push --force`.\n')
    (tmp_path / 'GLOBAL.md').write_text('- Never run `rm -rf /`.\n')
    return tmp_path


def test_cli_prints_a_redacted_report_and_a_short_summary(files):
    result = run(files, 'sessions.json', '--rules', 'project=AGENTS.md')
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report['profile'] == adherence.PROFILE and report['counts'] == dict(observed_violation=2)
    assert SECRET not in result.stdout and 'Do not edit' not in result.stdout and report['redaction'] == 'digest_only'
    assert 'observed_violation=2' in result.stderr and 'calibration' in result.stderr and len(result.stderr.splitlines()) <= 6


def test_cli_output_is_byte_identical_across_runs(files):
    first, second = (run(files, 'sessions.json', '--rules', 'project=AGENTS.md').stdout for _ in range(2))
    assert first == second != ''


def test_global_rules_are_refused_unless_the_scope_names_the_global_layer(files):
    refused = run(files, 'sessions.json', '--rules', 'global=GLOBAL.md')
    allowed = run(files, 'sessions.json', '--rules', 'global=GLOBAL.md', '--scope', 'project+global')
    assert refused.returncode == 2 and 'layer' in refused.stderr and refused.stdout == ''
    assert allowed.returncode == 0 and json.loads(allowed.stdout)['inventory']['sources'][0]['layer'] == 'global'


def test_private_detail_is_opt_in(files):
    report = json.loads(run(files, 'sessions.json', '--private-detail').stdout)
    assert report['redaction'] == 'private_detail' and report['findings'][0]['detail']['instruction'].startswith('Do not edit')


def test_output_file_is_created_exclusively_and_stdout_stays_empty(files):
    first = run(files, 'sessions.json', '--output', 'report.json')
    again = run(files, 'sessions.json', '--output', 'report.json')
    assert (first.returncode, first.stdout) == (0, '') and json.loads((files / 'report.json').read_text())['profile'] == adherence.PROFILE
    assert again.returncode == 2


@pytest.mark.parametrize('content', ['', '[]', '{"profile": "other", "sessions": []}', '{"profile": "%s", "sessions": [{}]}' % adherence.SESSIONS_PROFILE],
                         ids=['empty', 'not-an-object', 'wrong-profile', 'session-without-fields'])
def test_invalid_sessions_exit_two_without_a_report(files, content):
    (files / 'bad.json').write_text(content)
    result = run(files, 'bad.json')
    assert result.returncode == 2 and result.stdout == '' and 'instruction-adherence' in result.stderr


def test_missing_rule_file_exits_two(files):
    assert run(files, 'sessions.json', '--rules', 'project=MISSING.md').returncode == 2
