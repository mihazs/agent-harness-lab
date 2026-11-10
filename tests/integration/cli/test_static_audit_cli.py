"""Synthetic CLI coverage for the offline static audit."""
import json
import os
import socket
import subprocess
from pathlib import Path

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.sources import harness_inventory
from tests.support import harness_surface_fixture as fixture
from tests.unit.surfaces.test_static_audit import WELL_FORMED


def test_nested_task_reports_conditional_precedence(tmp_path, monkeypatch, capsys):
    project = tmp_path / 'project'
    (project / '.git').mkdir(parents=True)
    (project / 'src').mkdir()
    (project / 'AGENTS.md').write_text('Use option A.\n')
    (project / 'src/AGENTS.md').write_text('Use option B.\n')
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    assert main(['audit', str(project), '--task-cwd', 'src', '--json']) == 0
    report = json.loads(capsys.readouterr().out)['instruction_precedence']
    assert [(r['higher'], r['lower']) for r in report['conditional_relations']] == [('src/AGENTS.md', 'AGENTS.md')]
    assert report['effective'] == 'unknown'
    assert report['unchecked']['imports'] == 'not_checked'


@pytest.mark.parametrize('task', ['../outside', '/absolute', 'missing', 'link', '.git'])
def test_unsafe_task_directory_is_refused_before_report(tmp_path, monkeypatch, task):
    project = tmp_path / 'project'
    (project / '.git').mkdir(parents=True)
    (tmp_path / 'outside').mkdir()
    (project / 'link').symlink_to(tmp_path / 'outside', target_is_directory=True)
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    with pytest.raises(SystemExit) as exc:
        main(['audit', str(project), '--task-cwd', task])
    assert exc.value.code == 2
    assert not (tmp_path / 'state').exists()


@pytest.mark.parametrize('files', [{}, {'CLAUDE.md': '# Synthetic\n'}, WELL_FORMED], ids=['empty', 'minimal', 'well-formed'])
def test_json_report_is_stable_private_and_read_only(tmp_path, monkeypatch, capsys, files):
    project = tmp_path / 'project'
    project.mkdir()
    for name, content in files.items():
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    before = fixture.tree_state(project)
    def denied(*args, **kwargs):
        raise AssertionError('audit must not execute or connect')
    monkeypatch.setattr(subprocess, 'Popen', denied)
    monkeypatch.setattr(socket, 'socket', denied)
    monkeypatch.setattr(os, 'system', denied)
    assert main(['audit', str(project), '--runtime', 'claude-code', '--json']) == 0
    streams = capsys.readouterr()
    first = json.loads(streams.out)
    path = Path(streams.err.removeprefix('Private report: ').strip())
    assert json.loads(path.read_text()) == first
    assert path.stat().st_mode & 0o077 == 0
    assert main(['audit', str(project), '--runtime', 'claude-code', '--json']) == 0
    assert json.loads(capsys.readouterr().out) == first
    assert fixture.tree_state(project) == before
    assert first['performance_evidence'] is False and first['publication_allowed'] is False
    assert first['aggregate_score'] is None and 'ahl improve' in first['next_action']
    coverage = {row['dimension']: row['status'] for row in first['coverage']}
    assert coverage['skills'] == 'unknown'
    assert all(coverage[name] == 'not_checked' for name in ('sensors', 'memory_and_state', 'improvement_loop'))
    assert all(row['status'] in ('conforms', 'weak', 'missing', 'unknown', 'not_checked') for row in first['findings'])
    assert all(row['notice'] == 'convention_check_not_performance_evidence' for row in first['findings'])


def test_overflow_is_blocked_and_state_inside_project_is_refused(tmp_path, monkeypatch, capsys):
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'CLAUDE.md').write_text('# Synthetic\n')
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    monkeypatch.setattr(harness_inventory, 'MAX_FILES', 0)
    assert main(['audit', str(project), '--runtime', 'claude-code', '--json']) == 3
    report = json.loads(capsys.readouterr().out)
    assert report['status'] == 'blocked'
    assert 'blocked' in report['next_action']
    assert all(row['status'] != 'conforms' for row in report['coverage'])
    monkeypatch.setenv('XDG_STATE_HOME', str(project / 'state'))
    with pytest.raises(SystemExit) as exc:
        main(['audit', str(project)])
    assert exc.value.code == 2
    assert not (project / 'state').exists()


def test_unparsed_surface_is_unknown(tmp_path, monkeypatch, capsys):
    project = tmp_path / 'project'
    (project / '.claude').mkdir(parents=True)
    (project / '.claude/settings.json').write_text('{invalid')
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    assert main(['audit', str(project), '--runtime', 'claude-code', '--json']) == 0
    report = json.loads(capsys.readouterr().out)
    assert next(row for row in report['coverage'] if row['dimension'] == 'enforcement')['status'] == 'not_checked'
    assert all(row['status'] == 'not_checked' for row in report['findings'] if row['dimension'] == 'enforcement')


def test_next_action_references_a_convention_finding(tmp_path, monkeypatch, capsys):
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'CLAUDE.md').write_text('   \n')
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    assert main(['audit', str(project), '--runtime', 'claude-code', '--json']) == 0
    report = json.loads(capsys.readouterr().out)
    finding = next(row for row in report['findings'] if row['remediation'])
    assert finding['id'] in report['next_action']
    assert 'does not establish a performance improvement' in report['next_action']


@pytest.mark.parametrize(('header', 'expected'), [
    ('---\nname: demo\ndescription: Helps users. Use when asked for help.\n---\nBody\n', 'conforms'),
    ('---\nname: demo\n---\nBody\n', 'weak'),
    ('---\nname: demo\ndescription: {flow: map}\n---\nBody\n', 'not_checked'),
])
def test_skill_coverage_uses_skill_nodes_not_command_rows(tmp_path, monkeypatch, capsys, header, expected):
    project = tmp_path / 'project'
    path = project / '.claude/skills/demo/SKILL.md'
    path.parent.mkdir(parents=True)
    path.write_text(header)
    command = project / '.claude/commands/example.md'
    command.parent.mkdir(parents=True)
    command.write_text('---\nname: example\ndescription: An unrelated command\n---\nBody\n')
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    assert main(['audit', str(project), '--runtime', 'claude-code', '--json']) == 0
    report = json.loads(capsys.readouterr().out)
    assert next(row for row in report['coverage'] if row['dimension'] == 'skills')['status'] == expected
    assert all(row['status'] in ('conforms', 'weak', 'missing', 'unknown', 'not_checked') for row in report['findings'])
    assert all(row['dimension'] in ('instructions', 'skills', 'enforcement') for row in report['findings'])
