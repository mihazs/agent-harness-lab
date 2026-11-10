"""Synthetic CLI checks for bounded private instruction references."""
import json

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.audit import constraint_scan


def test_cli_constraint_references_do_not_expose_lines_or_secrets(tmp_path, monkeypatch, capsys):
    project = tmp_path / 'project'
    settings = project / '.claude/settings.json'
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({'permissions': {'deny': ['Edit(.env)']}}))
    (project / '.env').write_text('SYNTHETIC_SECRET')
    (project / 'CLAUDE.md').write_text('Never edit `.env`.\nSYNTHETIC_PRIVATE_INSTRUCTION\n')
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    assert main(['audit', str(project), '--runtime', 'claude-code', '--json']) == 0
    output = json.loads(capsys.readouterr().out)
    scan = output['constraint_references']
    assert scan['status'] == 'completed' and scan['coverage'] == 'unknown'
    entry = scan['files'][0]['constraints'][0]
    assert entry['protected_paths'] == [dict(path='.env', permission='permission_candidate_present')]
    assert entry['hook_match'] == entry['enforcement'] == 'unknown'
    assert 'SYNTHETIC_SECRET' not in json.dumps(output) and 'SYNTHETIC_PRIVATE_INSTRUCTION' not in json.dumps(output)
    assert scan['publication_allowed'] is False


def test_private_instruction_is_not_read_or_treated_as_complete(tmp_path, monkeypatch, capsys):
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'CLAUDE.local.md').write_text('Never disclose SYNTHETIC_PRIVATE_LOCAL')
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    assert main(['audit', str(project), '--runtime', 'claude-code', '--json']) == 0
    output = json.loads(capsys.readouterr().out)
    assert output['constraint_references']['status'] == 'incomplete'
    assert output['constraint_references']['files'] == []
    assert 'SYNTHETIC_PRIVATE_LOCAL' not in json.dumps(output)


def test_changed_instruction_fails_closed_without_returning_text(tmp_path, monkeypatch, capsys):
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'CLAUDE.md').write_text('Never edit `.env`.\n')
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    class ChangedFolder(constraint_scan.Folder):
        def read(self, name, maximum=65536, *, prefix=False):
            raw = super().read(name, maximum, prefix=prefix)
            return raw + b'SYNTHETIC_CHANGED' if raw is not None else None
    monkeypatch.setattr(constraint_scan, 'Folder', ChangedFolder)
    assert main(['audit', str(project), '--runtime', 'claude-code', '--json']) == 0
    output = json.loads(capsys.readouterr().out)
    assert output['constraint_references']['status'] == 'incomplete'
    assert output['constraint_references']['files'] == []
    assert 'SYNTHETIC_CHANGED' not in json.dumps(output)


def test_source_limit_leaves_constraint_coverage_unknown(tmp_path, monkeypatch, capsys):
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'CLAUDE.md').write_text('Never edit `.env`.\n')
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    monkeypatch.setattr(constraint_scan, 'MAX_SOURCES', 0)
    assert main(['audit', str(project), '--runtime', 'claude-code', '--json']) == 0
    scan = json.loads(capsys.readouterr().out)['constraint_references']
    assert (scan['status'], scan['coverage'], scan['files']) == ('incomplete', 'unknown', [])


def test_failed_read_still_consumes_shared_byte_budget(tmp_path, monkeypatch, capsys):
    project = tmp_path / 'project'
    (project / 'src').mkdir(parents=True)
    raw = 'Never edit `.env`.\n'
    (project / 'CLAUDE.md').write_text(raw)
    (project / 'src/CLAUDE.md').write_text(raw)
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    monkeypatch.setattr(constraint_scan, 'MAX_TOTAL_BYTES', len(raw.encode()))
    calls = []
    class FailingFolder(constraint_scan.Folder):
        def read(self, name, maximum=65536, *, prefix=False):
            calls.append(name)
            super().read(name, maximum, prefix=prefix)
            raise ValueError('synthetic failure after read')
    monkeypatch.setattr(constraint_scan, 'Folder', FailingFolder)
    assert main(['audit', str(project), '--runtime', 'claude-code', '--json']) == 0
    scan = json.loads(capsys.readouterr().out)['constraint_references']
    assert len(calls) == 1 and scan['status'] == 'incomplete' and scan['unavailable'] == 2


@pytest.mark.parametrize('refused', ['CLAUDE.md', 'subtree'])
def test_refused_instruction_or_subtree_leaves_scan_incomplete(tmp_path, monkeypatch, capsys, refused):
    project = tmp_path / 'project'
    project.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'CLAUDE.md').write_text('Never disclose SYNTHETIC_REFUSED')
    (project / refused).symlink_to(outside / 'CLAUDE.md' if refused == 'CLAUDE.md' else outside)
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    assert main(['audit', str(project), '--runtime', 'claude-code', '--json']) == 0
    output = json.loads(capsys.readouterr().out)
    scan = output['constraint_references']
    assert scan['status'] == 'incomplete' and scan['unavailable'] >= 1 and scan['files'] == []
    assert 'SYNTHETIC_REFUSED' not in json.dumps(output)
