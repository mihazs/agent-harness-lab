"""Synthetic lexical hook candidates never assert path enforcement or execution."""
import json
import socket
import subprocess

import pytest

from agent_harness_lab.cli import main


@pytest.mark.parametrize(('matcher', 'disabled', 'expected'), [
    ('Edit', False, 'hook_candidate_present'), ('Edit|Write', False, 'hook_candidate_present'),
    ('*', False, 'hook_candidate_present'), ('Bash', False, 'unknown'), ('Edit', True, 'unknown'),
    ('Edit.*', False, 'unknown')])
def test_pretool_matcher_is_candidate_only(tmp_path, monkeypatch, capsys, matcher, disabled, expected):
    project = tmp_path / 'project'
    settings = project / '.claude/settings.json'
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({'disableAllHooks': disabled, 'hooks': {'PreToolUse': [
        {'matcher': matcher, 'hooks': [{'type': 'command', 'command': 'never-run'}]}]}}))
    (project / 'CLAUDE.md').write_text('Never edit `.env`.\nNever read `.env`.\n')
    (project / '.env').write_text('SYNTHETIC_SECRET')
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **kw: pytest.fail('hook executed'))
    monkeypatch.setattr(socket, 'socket', lambda *a, **kw: pytest.fail('network used'))
    assert main(['audit', str(project), '--runtime', 'claude-code', '--json']) == 0
    report = json.loads(capsys.readouterr().out)
    rows = report['constraint_references']['files'][0]['constraints']
    assert rows[0]['hook_match'] == expected and rows[1]['hook_match'] == 'unknown'
    assert all(row['enforcement'] == 'unknown' for row in rows)
    if expected == 'hook_candidate_present':
        assert rows[0]['hook_candidate']['matcher_scope'] == 'tool_name_only'
        assert rows[0]['hook_candidate']['path_enforcement'] == 'unknown'
        assert rows[0]['hook_candidate']['observed_execution'] == 'unknown'
    assert 'SYNTHETIC_SECRET' not in json.dumps(report) and 'never-run' not in json.dumps(report)


def test_codex_hook_matcher_remains_unknown(tmp_path, monkeypatch, capsys):
    project = tmp_path / 'project'
    settings = project / '.codex/hooks.json'
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({'hooks': {'PreToolUse': [{'matcher': 'apply_patch',
        'hooks': [{'type': 'command', 'command': 'never-run'}]}]}}))
    (project / 'AGENTS.md').write_text('Never edit `.env`.\n')
    (project / '.env').write_text('synthetic')
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    assert main(['audit', str(project), '--runtime', 'codex', '--json']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['constraint_references']['files'][0]['constraints'][0]['hook_match'] == 'unknown'
    assert 'PreToolUse' in report['hook_events']['declared']
