"""`ahl audit` lists the harness profile findings beside the convention checks (#1113); synthetic checkouts only."""
import json

import pytest

from agent_harness_lab.cli import main
from tests.support.profile_fixture import build, package


@pytest.fixture(autouse=True)
def state(monkeypatch, tmp_path):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))


def checkout(tmp_path, instruction):
    root = build(tmp_path, {'package.json': package({'test': 'vitest run'}), 'pnpm-lock.yaml': '', 'AGENTS.md': instruction})
    (root / '.git').mkdir()
    return root


def test_json_report_carries_the_profile_and_findings(tmp_path, capsys):
    assert main(['audit', str(checkout(tmp_path, 'Run `pnpm run deploy`. Use pnpm.\n')), '--json']) == 0
    value = json.loads(capsys.readouterr().out)
    section = value['harness_profile']
    assert (section['status'], section['publication_allowed'], section['authority']) == ('compared', False, 'none')
    assert [(row['kind'], row['subject']) for row in section['findings']] == [('command_target_missing', 'pnpm deploy'), ('verification_command_unnamed', 'test')]
    assert section['project']['package_managers'][0]['name'] == 'pnpm'
    assert value['model_calls'] == 0 and value['files_written'] == 0


def test_the_summary_prints_each_finding_with_its_id_and_proposed_update(tmp_path, capsys):
    assert main(['audit', str(checkout(tmp_path, 'Run `pnpm run deploy`. Use pnpm.\n'))]) == 0
    out = capsys.readouterr().out
    assert 'harness contradiction: command_target_missing "pnpm deploy" (hp/command_target_missing/' in out and 'correct_or_remove_line' in out
    assert 'harness gap: verification_command_unnamed "test"' in out
    assert '2 harness profile finding(s)' in out


def test_a_clean_checkout_prints_no_harness_finding_and_keeps_its_next_action(tmp_path, capsys):
    assert main(['audit', str(checkout(tmp_path, 'Use pnpm. Run `pnpm test` for the tests.\n'))]) == 0
    out = capsys.readouterr().out
    assert 'harness contradiction' not in out and 'harness gap' not in out and 'harness profile finding' not in out


def test_a_checkout_without_an_instruction_file_is_not_reported_clean(tmp_path, capsys):
    root = build(tmp_path, {'package.json': package({'test': 'x'})})
    (root / '.git').mkdir()
    assert main(['audit', str(root), '--json']) == 0
    section = json.loads(capsys.readouterr().out)['harness_profile']
    assert (section['status'], section['findings']) == ('no_instruction_file', [])


def test_decision_findings_reach_the_audit_and_the_brief_section(tmp_path, capsys):
    from agent_harness_lab.project_profile import report
    root = build(tmp_path, {'package.json': package({'test': 'jest'}), 'docs/adr/0002-use-pnpm.md': '# 2. Use pnpm\n\n## Status\n\nAccepted\n',
                            'AGENTS.md': 'Run `npm test`. Run `jest`.\n'})
    (root / '.git').mkdir()
    assert main(['audit', str(root)]) == 0
    out = capsys.readouterr().out
    assert 'harness contradiction: decision_contradicted "npm"' in out and 'harness gap: decision_records_unstated "decision_records"' in out
    listed = report.brief_section(report.build(root))['findings']
    assert [(row['kind'], row['finding_class']) for row in listed if row['kind'].startswith('decision')] == [
        ('decision_contradicted', 'contradiction'), ('decision_records_unstated', 'gap')]
