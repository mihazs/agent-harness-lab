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


def test_audit_reports_private_protected_path_metadata_without_reading_secret(tmp_path, monkeypatch, capsys):
    project = tmp_path / 'project'
    project.mkdir()
    (project / '.env').write_text('SYNTHETIC_SECRET')
    (project / 'eslint.config.js').write_text('synthetic config')
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    original_open = os.open
    def safe_open(path, flags, *args, **kwargs):
        if os.fspath(path) == '.env': pytest.fail('secret file opened')
        return original_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(os, 'open', safe_open)
    assert main(['audit', str(project), '--json']) == 0
    report = json.loads(capsys.readouterr().out)
    inventory = report['protected_path_inventory']
    assert {row['path'] for row in inventory['paths']} == {'.env', 'eslint.config.js'}
    assert all(row['enforcement'] == 'unknown' and row['content'] == 'not_read' for row in inventory['paths'])
    assert 'SYNTHETIC_SECRET' not in json.dumps(report)
    assert inventory['publication_allowed'] is False


def test_audit_permission_deny_is_a_candidate_not_enforcement(tmp_path, monkeypatch, capsys):
    project = tmp_path / 'project'
    settings = project / '.claude/settings.json'
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({'permissions': {'deny': ['Edit(.env)', 'Bash(SYNTHETIC_PRIVATE_COMMAND)']}}))
    (project / '.env').write_text('SYNTHETIC_SECRET')
    (project / 'eslint.config.js').write_text('synthetic')
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    assert main(['audit', str(project), '--runtime', 'claude-code', '--json']) == 0
    report = json.loads(capsys.readouterr().out)
    rows = {row['path']: row for row in report['permission_candidates']['paths']}
    assert rows['.env']['status'] == 'permission_candidate_present'
    assert rows['eslint.config.js']['status'] == 'unknown'
    assert report['permission_candidates']['effective'] == 'unknown'
    assert all(row['enforcement'] == 'unknown' for row in rows.values())
    assert 'SYNTHETIC_SECRET' not in json.dumps(report) and 'SYNTHETIC_PRIVATE_COMMAND' not in json.dumps(report)


@pytest.mark.parametrize('declared', [True, False])
def test_protected_path_hook_review_is_tool_only_and_unknown(tmp_path, monkeypatch, capsys, declared):
    project = tmp_path / 'project'
    settings = project / '.claude/settings.json'
    settings.parent.mkdir(parents=True)
    hooks = {'PreToolUse': [{'matcher': 'Edit', 'hooks': [{'type': 'command', 'command': 'never-run'}]}]} if declared else {}
    settings.write_text(json.dumps({'hooks': hooks}))
    (project / '.env').write_text('SYNTHETIC_SECRET')
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **kw: pytest.fail('hook executed'))
    assert main(['audit', str(project), '--runtime', 'claude-code', '--json']) == 0
    report = json.loads(capsys.readouterr().out)
    review = report['protected_path_hook_review']
    assert review['status'] == ('tool_hook_candidate_present' if declared else 'unknown')
    assert review['path_enforcement'] == 'unknown' and review['publication_allowed'] is False
    assert (review['candidate']['matcher_scope'] == 'tool_name_only') if declared else review['candidate'] is None
    assert 'review' in review['next_action'].lower()
    assert 'never-run' not in json.dumps(report) and 'SYNTHETIC_SECRET' not in json.dumps(report)
    assert main(['audit', str(project), '--runtime', 'claude-code']) == 0
    output = capsys.readouterr().out
    assert f"Write-tool hook candidate: {review['status']}; path enforcement: unknown" in output
    assert review['next_action'] in output


def test_human_protected_path_output_escapes_control_characters(tmp_path, monkeypatch, capsys):
    project = tmp_path / 'project'
    project.mkdir()
    (project / '.env.\x1b[31m').write_text('synthetic')
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    assert main(['audit', str(project)]) == 0
    output = capsys.readouterr().out
    assert '\x1b' not in output and '\\u001b' in output


@pytest.mark.parametrize(('runtime', 'path'), [('claude-code', '.claude/settings.json'), ('codex', '.codex/hooks.json')])
def test_declared_hook_events_are_reported_without_execution(tmp_path, monkeypatch, capsys, runtime, path):
    project = tmp_path / 'project'
    target = project / path
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({'hooks': {event: [{'hooks': [{'type': 'command', 'command': 'never-run'}]}]
                                           for event in ('PreToolUse', 'SessionStart', 'PreCompact')}}))
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **kw: pytest.fail('hook executed'))
    monkeypatch.setattr(socket, 'socket', lambda *a, **kw: pytest.fail('network used'))
    assert main(['audit', str(project), '--runtime', runtime, '--json']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['hook_events'] == dict(declared=['PreCompact', 'PreToolUse', 'SessionStart'],
                                         coverage='declared_only', observed_execution='unknown')
    assert result['publication_allowed'] is False


def test_unreadable_hook_event_coverage_stays_unknown(tmp_path, monkeypatch, capsys):
    project = tmp_path / 'project'
    target = project / '.claude/settings.json'
    target.parent.mkdir(parents=True)
    target.write_text('{bad')
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    assert main(['audit', str(project), '--runtime', 'claude-code', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['hook_events'] == dict(declared=[], coverage='unknown', observed_execution='unknown')


@pytest.mark.parametrize('groups', [[], [{'hooks': []}]])
def test_unknown_hook_event_without_handlers_keeps_coverage_unknown(tmp_path, monkeypatch, capsys, groups):
    target = tmp_path / 'project' / '.claude/settings.json'
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({'hooks': {'SyntheticUnknownEvent': groups,
        'SessionStart': [{'hooks': [{'type': 'command', 'command': 'never-run'}]}]}}))
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **kw: pytest.fail('hook executed'))
    assert main(['audit', str(target.parent.parent), '--runtime', 'claude-code', '--json']) == 0
    events = json.loads(capsys.readouterr().out)['hook_events']
    assert events == dict(declared=['SessionStart'], coverage='unknown', observed_execution='unknown')


@pytest.mark.parametrize('path', ['.claude/skills/sample/SKILL.md', '.claude/agents/sample.md'])
def test_embedded_hooks_leave_event_coverage_unknown(tmp_path, monkeypatch, capsys, path):
    project = tmp_path / 'project'
    target = project / path
    target.parent.mkdir(parents=True)
    target.write_text('---\nname: sample\ndescription: synthetic\nhooks:\n  PreToolUse:\n'
                      '    - hooks:\n        - type: command\n          command: SYNTHETIC_SECRET\n---\n')
    settings = project / '.claude/settings.json'
    settings.write_text(json.dumps({'hooks': {'SessionStart': [{'hooks': [
        {'type': 'command', 'command': 'never-run'}]}]}}))
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **kw: pytest.fail('hook executed'))
    assert main(['audit', str(project), '--runtime', 'claude-code', '--json']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['hook_events'] == dict(declared=['SessionStart'], coverage='unknown', observed_execution='unknown')
    assert 'SYNTHETIC_SECRET' not in json.dumps(report) and 'never-run' not in json.dumps(report)


def test_codex_agent_hooks_leave_event_coverage_unknown(tmp_path, monkeypatch, capsys):
    project = tmp_path / 'project'
    agent = project / '.codex/agents/sample.toml'
    agent.parent.mkdir(parents=True)
    agent.write_text('name = "sample"\ndescription = "synthetic"\n'
                     'developer_instructions = "synthetic"\n[hooks]\nPreToolUse = []\n')
    (project / '.codex/hooks.json').write_text(json.dumps({'hooks': {'SessionStart': [
        {'hooks': [{'type': 'command', 'command': 'never-run'}]}]}}))
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    assert main(['audit', str(project), '--runtime', 'codex', '--json']) == 0
    events = json.loads(capsys.readouterr().out)['hook_events']
    assert events == dict(declared=['SessionStart'], coverage='unknown', observed_execution='unknown')


@pytest.mark.parametrize(('enabled', 'coverage'), [(True, 'unknown'), (False, 'declared_only')])
def test_unselected_enabled_plugin_leaves_event_coverage_unknown(tmp_path, monkeypatch, capsys, enabled, coverage):
    project = tmp_path / 'project'
    settings = project / '.claude/settings.json'
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({'enabledPlugins': {'synthetic@market': enabled}, 'hooks': {'SessionStart': [
        {'hooks': [{'type': 'command', 'command': 'never-run'}]}]}}))
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **kw: pytest.fail('hook executed'))
    monkeypatch.setattr(socket, 'socket', lambda *a, **kw: pytest.fail('network used'))
    assert main(['audit', str(project), '--runtime', 'claude-code', '--json']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['hook_events'] == dict(declared=['SessionStart'], coverage=coverage, observed_execution='unknown')
    assert 'never-run' not in json.dumps(report)


def test_codex_unselected_plugin_leaves_event_coverage_unknown(tmp_path, monkeypatch, capsys):
    project = tmp_path / 'project'
    config = project / '.codex/config.toml'
    config.parent.mkdir(parents=True)
    config.write_text('[plugins."synthetic@market"]\nenabled = true\n')
    (config.parent / 'hooks.json').write_text(json.dumps({'hooks': {'SessionStart': [
        {'hooks': [{'type': 'command', 'command': 'never-run'}]}]}}))
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    monkeypatch.setattr(socket, 'socket', lambda *a, **kw: pytest.fail('network used'))
    assert main(['audit', str(project), '--runtime', 'codex', '--json']) == 0
    events = json.loads(capsys.readouterr().out)['hook_events']
    assert events == dict(declared=['SessionStart'], coverage='unknown', observed_execution='unknown')


def test_excluded_hook_source_keeps_known_events_but_coverage_unknown(tmp_path, monkeypatch, capsys):
    project = tmp_path / 'project'
    target = project / '.claude/settings.json'
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({'hooks': {'PreCompact': [{'hooks': [{'type': 'command', 'command': 'never-run'}]}]}}))
    (target.parent / 'settings.local.json').write_text('{"hooks": {}}')
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    assert main(['audit', str(project), '--runtime', 'claude-code', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['hook_events'] == dict(declared=['PreCompact'], coverage='unknown', observed_execution='unknown')


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


@pytest.mark.parametrize(('extra', 'expected'), [(0, 'weak'), (1, 'missing')])
def test_codex_chain_dropped_bytes_are_missing_coverage(tmp_path, monkeypatch, capsys, extra, expected):
    project = tmp_path / 'project'
    (project / '.git').mkdir(parents=True)
    (project / 'src').mkdir()
    (project / 'AGENTS.md').write_bytes(b'a' * 20000)
    (project / 'src/AGENTS.md').write_bytes(b'b' * (32768 - 20000 + extra))
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    assert main(['audit', str(project), '--task-cwd', 'src', '--json']) == 0
    report = json.loads(capsys.readouterr().out)
    assert next(row for row in report['coverage'] if row['dimension'] == 'instructions')['status'] == expected
    missing = [row for row in report['findings'] if row['status'] == 'missing']
    if extra:
        assert len(missing) == 1
        assert missing[0]['path'] == 'src/AGENTS.md'
        assert dict(missing[0]['measures'])['dropped_bytes'] == 1
    else:
        assert not missing
        assert {'instruction_constraints_not_detected', 'instruction_verification_command_not_detected'} <= {
            row['reason'] for row in report['findings']}
    assert report['publication_allowed'] is False


def test_unsettled_dropped_count_does_not_claim_missing_coverage(tmp_path, monkeypatch, capsys):
    project = tmp_path / 'project'
    (project / '.git').mkdir(parents=True)
    (project / 'src').mkdir()
    (project / 'AGENTS.md').write_bytes(b'a' * (32768 - 10))
    (project / 'src/AGENTS.md').write_text('\u3000' * 20 + 'content')
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    assert main(['audit', str(project), '--task-cwd', 'src', '--json']) == 0
    report = json.loads(capsys.readouterr().out)
    assert next(row for row in report['coverage'] if row['dimension'] == 'instructions')['status'] == 'weak'
    assert not any(row['status'] == 'missing' for row in report['findings'])


def test_claude_declared_totals_include_import_without_exposing_content(tmp_path, monkeypatch, capsys):
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'CLAUDE.md').write_text('@details.md\n')
    (project / 'details.md').write_text('SYNTHETIC_SECRET_CONTENT\n')
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    assert main(['audit', str(project), '--runtime', 'claude-code', '--json']) == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    total = report['declared_instruction_totals']
    assert (total['status'], total['files'], total['lines']) == ('declared', 2, 2)
    assert total['observed_loading'] == 'unknown' and not total['publication_allowed']
    assert 'SYNTHETIC_SECRET_CONTENT' not in output


@pytest.mark.parametrize(('budget', 'weak'), [(2, False), (1, True)])
def test_claude_line_budget_is_configurable_convention(tmp_path, monkeypatch, capsys, budget, weak):
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'CLAUDE.md').write_text('@details.md\n')
    (project / 'details.md').write_text('synthetic\n')
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    assert main(['audit', str(project), '--runtime', 'claude-code', '--json',
                 '--instruction-soft-lines', str(budget)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['declared_instruction_totals']['soft_line_budget'] == budget
    finding = [r for r in report['findings'] if r['reason'] == 'instruction_launch_lines_above_soft_budget']
    assert bool(finding) is weak
    if weak:
        assert finding[0]['status'] == 'weak' and finding[0]['evidence_kind'] == 'count'
        assert dict(finding[0]['measures']) == {'lines': 2, 'soft_line_budget': budget}
        assert finding[0]['notice'] == 'convention_check_not_performance_evidence'


def test_claude_shape_hypotheses_and_incomplete_closure(tmp_path, monkeypatch, capsys):
    project = tmp_path / 'project'
    project.mkdir()
    source = project / 'CLAUDE.md'
    source.write_text('# Procedure\n' + ''.join(f'{i}. step\n' for i in range(1, 6)))
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    assert main(['audit', str(project), '--runtime', 'claude-code', '--json']) == 0
    report = json.loads(capsys.readouterr().out)
    expected = {'instruction_constraints_not_detected', 'instruction_verification_command_not_detected',
                'instruction_procedure_section_detected'}
    assert expected <= {r['reason'] for r in report['findings'] if r['status'] == 'weak'}
    source.write_text('@missing.md\n')
    assert main(['audit', str(project), '--runtime', 'claude-code', '--json']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['declared_instruction_totals']['status'] == 'unknown'
    assert not expected.intersection(r['reason'] for r in report['findings'])


def test_import_change_between_inventory_and_resolver_is_an_unbound_observation(tmp_path, monkeypatch, capsys):
    from agent_harness_lab import audit

    project = tmp_path / 'project'
    project.mkdir()
    (project / 'CLAUDE.md').write_text('@details.md\n')
    imported = project / 'details.md'
    imported.write_text('old\n')
    original = audit.audit
    def change_after_inventory(*args, **kwargs):
        imported.write_text('new\n' * 10)
        return original(*args, **kwargs)
    monkeypatch.setattr(audit, 'audit', change_after_inventory)
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    assert main(['audit', str(project), '--runtime', 'claude-code', '--json']) == 0
    total = json.loads(capsys.readouterr().out)['declared_instruction_totals']
    assert total['lines'] == 11
    assert total['snapshot_relation'] == 'separate_resolver_observation'
    assert total['inventory_comparison'] == 'recognized_instruction_files_only'
    assert total['cross_observation_consistency'] == 'unknown'


@pytest.mark.parametrize(('limit', 'expected', 'dropped'), [(50000, 'weak', None), (20000, 'missing', 13000)])
def test_declared_codex_budget_overrides_assumed_default(tmp_path, monkeypatch, capsys, limit, expected, dropped):
    project = tmp_path / 'project'
    (project / '.git').mkdir(parents=True)
    (project / 'AGENTS.md').write_bytes(b'x' * 33000)
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    assert main(['audit', str(project), '--json', '--codex-project-doc-max-bytes', str(limit)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['codex_budget_declaration'] == dict(bytes=limit, origin='caller_declared',
                                                       runtime_configuration='unknown', publication_allowed=False)
    assert next(r for r in report['coverage'] if r['dimension'] == 'instructions')['status'] == expected
    found = [r for r in report['findings'] if r['status'] == 'missing']
    if dropped is None:
        assert not found
        assert {'instruction_constraints_not_detected', 'instruction_verification_command_not_detected'} <= {
            row['reason'] for row in report['findings']}
    else:
        assert len(found) == 1 and found[0]['path'] == 'AGENTS.md'
        assert dict(found[0]['measures'])['dropped_bytes'] == dropped
        assert found[0]['reason'] == 'instruction_chain_truncated_at_declared_project_doc_bytes'


def test_codex_budget_rejected_for_claude(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    with pytest.raises(SystemExit) as error:
        main(['audit', str(tmp_path), '--runtime', 'claude-code', '--codex-project-doc-max-bytes', '1'])
    assert error.value.code == 2


def test_codex_declared_total_counts_selected_checkout_chain(tmp_path, monkeypatch, capsys):
    project = tmp_path / 'project'
    (project / '.git').mkdir(parents=True)
    (project / 'src').mkdir()
    (project / 'AGENTS.md').write_text('Must test.\n')
    (project / 'src/AGENTS.override.md').write_text('pytest\n')
    (project / 'src/AGENTS.md').write_text('SYNTHETIC_SHADOWED\n')
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    assert main(['audit', str(project), '--task-cwd', 'src', '--json', '--instruction-soft-lines', '1']) == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    total = report['declared_instruction_totals']
    assert (total['status'], total['bytes'], total['lines'], total['files']) == ('declared', 18, 2, 2)
    assert total['scope'] == 'captured_checkout_project_chain'
    assert total['cross_observation_consistency'] == 'unknown' and total['observed_loading'] == 'unknown'
    assert total['publication_allowed'] is False and 'SYNTHETIC_SHADOWED' not in output
    assert any(r['reason'] == 'instruction_launch_lines_above_soft_budget' for r in report['findings'])


def test_codex_total_is_unknown_for_truncation_or_unsettled_root(tmp_path, monkeypatch, capsys):
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'AGENTS.md').write_text('x\n' * 10)
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    assert main(['audit', str(project), '--json']) == 0
    total = json.loads(capsys.readouterr().out)['declared_instruction_totals']
    assert total['status'] == 'unknown' and total['bytes'] is None
    assert 'project_root_unsettled' in total['reasons']
    (project / '.git').mkdir()
    assert main(['audit', str(project), '--json', '--codex-project-doc-max-bytes', '5']) == 0
    total = json.loads(capsys.readouterr().out)['declared_instruction_totals']
    assert total['status'] == 'unknown' and total['lines'] is None
    assert 'truncated_chain_not_counted' in total['reasons']


def test_codex_total_does_not_assume_checkout_root_above_nested_project(tmp_path, monkeypatch, capsys):
    project = tmp_path / 'project'
    (project / '.git').mkdir(parents=True)
    (project / 'src/.git').mkdir(parents=True)
    (project / 'AGENTS.md').write_text('root\n')
    (project / 'src/AGENTS.md').write_text('nested\n')
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    with pytest.raises(SystemExit) as error:
        main(['audit', str(project), '--task-cwd', 'src', '--json'])
    assert error.value.code == 2


def test_codex_total_does_not_trust_changed_selected_file(tmp_path, monkeypatch, capsys):
    from agent_harness_lab import audit

    project = tmp_path / 'project'
    (project / '.git').mkdir(parents=True)
    selected = project / 'AGENTS.md'
    selected.write_text('original\n')
    original_audit = audit.audit
    def change_after_inventory(*args, **kwargs):
        selected.write_text('changed\n')
        return original_audit(*args, **kwargs)
    monkeypatch.setattr(audit, 'audit', change_after_inventory)
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    assert main(['audit', str(project), '--json']) == 0
    total = json.loads(capsys.readouterr().out)['declared_instruction_totals']
    assert total['status'] == 'unknown' and 'inventory_content_changed' in total['reasons']
    assert total['bytes'] is None and total['cross_observation_consistency'] == 'unknown'


def test_codex_minimal_contract_and_procedure_hypotheses(tmp_path, monkeypatch, capsys):
    project = tmp_path / 'project'
    (project / '.git').mkdir(parents=True)
    source = project / 'AGENTS.md'
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    reasons = {'instruction_constraints_not_detected', 'instruction_verification_command_not_detected',
               'instruction_procedure_section_detected'}
    source.write_text('Must run pytest.\n')
    assert main(['audit', str(project), '--json']) == 0
    report = json.loads(capsys.readouterr().out)
    assert not reasons.intersection(row['reason'] for row in report['findings'])
    assert next(row for row in report['coverage'] if row['dimension'] == 'instructions')['status'] == 'conforms'
    source.write_text('# SYNTHETIC_PRIVATE_HEADING\n' + ''.join(f'{n}. step\n' for n in range(1, 6)))
    assert main(['audit', str(project), '--json']) == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    assert reasons <= {row['reason'] for row in report['findings'] if row['status'] == 'weak'}
    assert 'SYNTHETIC_PRIVATE_HEADING' not in output
    assert report['publication_allowed'] is False
    assert main(['audit', str(project), '--json', '--codex-project-doc-max-bytes', '2']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['declared_instruction_totals']['status'] == 'unknown'
    assert not reasons.intersection(row['reason'] for row in report['findings'])


def test_claude_agents_import_contributes_to_contract(tmp_path, monkeypatch, capsys):
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'CLAUDE.md').write_text('@AGENTS.md\n')
    (project / 'AGENTS.md').write_text('Must run pytest.\n')
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    assert main(['audit', str(project), '--runtime', 'claude-code', '--json']) == 0
    report = json.loads(capsys.readouterr().out)
    total = report['declared_instruction_totals']
    assert (total['status'], total['files'], total['lines']) == ('declared', 2, 2)
    assert total['shape']['constraints'] and total['shape']['verification_commands']
    assert not {'instruction_constraints_not_detected', 'instruction_verification_command_not_detected'}.intersection(
        row['reason'] for row in report['findings'])


def test_claude_empty_and_missing_import_diagnostics(tmp_path, monkeypatch, capsys):
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'CLAUDE.md').write_text('@detail.md\n')
    detail = project / 'detail.md'
    detail.write_text('  \n')
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    assert main(['audit', str(project), '--runtime', 'claude-code', '--json']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['declared_instruction_totals']['empty_import'] is True
    assert any(row['reason'] == 'instruction_empty_import' and row['status'] == 'weak' for row in report['findings'])
    detail.unlink()
    assert main(['audit', str(project), '--runtime', 'claude-code', '--json']) == 0
    total = json.loads(capsys.readouterr().out)['declared_instruction_totals']
    assert total['status'] == 'unknown' and 'import_missing' in total['reasons']


def test_empty_import_hypothesis_abstains_when_closure_unsettled(tmp_path, monkeypatch, capsys):
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'CLAUDE.md').write_text('@empty.md\n@missing.md\n')
    (project / 'empty.md').write_text('  \n')
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    assert main(['audit', str(project), '--runtime', 'claude-code', '--json']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['declared_instruction_totals']['status'] == 'unknown'
    assert not any(row['reason'] == 'instruction_empty_import' for row in report['findings'])


def test_instruction_improvement_hint_contains_only_fixed_content(tmp_path, monkeypatch, capsys):
    project = tmp_path / 'project'
    (project / '.git').mkdir(parents=True)
    (project / 'AGENTS.md').write_text('SYNTHETIC_PRIVATE_SENTENCE\n')
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    assert main(['audit', str(project), '--json']) == 0
    report = json.loads(capsys.readouterr().out)
    hint = report['improvement_hint']
    assert hint == dict(reason='instruction_constraints_not_detected',
                        focus='Investigate whether project instruction constraints need clarification.',
                        runtime='codex', status='hypothesis', authority='none', publication_allowed=False)
    assert 'SYNTHETIC_PRIVATE_SENTENCE' not in json.dumps(hint)
    assert str(project) not in json.dumps(hint)
    assert 'Select an improvement runtime compatible with the audited codex' in report['next_action']
    assert '--agent' not in report['next_action'] and '--focus' not in report['next_action']


def test_focus_hint_does_not_override_controlled_runtime(tmp_path, monkeypatch, capsys):
    from agent_harness_lab import config

    project = tmp_path / 'project'
    project.mkdir()
    (project / 'AGENTS.md').write_text('synthetic content\n')
    (project / '.git').mkdir()
    runtime = dict(name='responses', inference_settings=str(tmp_path / 'settings.json'),
                   cgroup_root=str(tmp_path / 'cgroup'), image_root=str(tmp_path / 'image'),
                   image_sha256='a' * 64, disk_bytes=67108864)
    settings = config.prepare(dict(profile='cooperative', runtime=runtime))
    config.create(project, settings)
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    assert main(['audit', str(project), '--runtime', 'codex', '--json']) == 0
    report = json.loads(capsys.readouterr().out)
    hint = report['improvement_hint']
    assert hint['runtime'] == 'codex' and hint['focus'] == (
        'Investigate whether project instruction constraints need clarification.')
    assert '--agent' not in report['next_action'] and '--focus' not in report['next_action']
    assert 'configured profile' in report['next_action']
    selected = config.execution_options(project, settings, {'agent': 'auto', 'inference_settings': None}, set())
    assert selected['agent'] == 'responses'
