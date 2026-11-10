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
    assert all(coverage[name] == 'not_checked' for name in ('skills', 'sensors', 'memory_and_state', 'improvement_loop'))
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
    assert all(row['status'] == 'unknown' for row in report['dimensions'])
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
    assert next(row for row in report['dimensions'] if row['dimension'] == 'hooks')['status'] == 'unknown'
    assert next(row for row in report['coverage'] if row['dimension'] == 'enforcement')['status'] == 'not_checked'


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
