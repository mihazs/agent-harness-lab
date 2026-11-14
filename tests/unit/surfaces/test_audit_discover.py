"""Automatic static convention discovery states what it evaluated and what it could not settle (#332).

Synthetic projects under tmp_path only; discovery is read-only and calls no model.
"""
from types import SimpleNamespace

import pytest

from agent_harness_lab.audit import chain, improvement_hint, recheck
from agent_harness_lab.audit.recheck import discover

LINE = 'synthetic instruction line\n'
PROCEDURE_SECTION = '# Release\n' + ''.join(f'{number}. step {number}\n' for number in range(1, 6))
SKILL = '---\nname: {name}\ndescription: Extract PDF documents. Use when asked to extract PDF documents.\n---\n\nInstructions.\n'
OVERSIZED_FOCUS = 'Investigate whether the oversized project instruction file can be simplified safely.'
INSTRUCTION_FILE = {'codex': 'AGENTS.md', 'claude': 'CLAUDE.md'}


def build(tmp_path, files, *, git=True):
    project = tmp_path/'project'; project.mkdir()
    if git: (project/'.git').mkdir()
    for name, text in files.items():
        path = project/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(text)
    return project


def skills(root):
    return {f'{root}/{name}/SKILL.md': SKILL.format(name=name) for name in ('alpha', 'beta')}


@pytest.mark.parametrize('agent, files, git, reason, focus', [
    pytest.param('codex', {'AGENTS.md': 'y'*40000 + '\n'}, True, chain.TRUNCATED, None, id='codex-chain-truncated-at-default-bytes'),
    pytest.param('codex', {'AGENTS.md': 'y'*40000 + '\n'}, False, 'instruction_exceeds_project_doc_bytes', OVERSIZED_FOCUS,
                 id='codex-oversized-file-without-project-root'),
    pytest.param('claude', {'CLAUDE.md': 'y'*(4*1024*1024 + 1) + '\n'}, True, 'instruction_exceeds_launch_bytes', OVERSIZED_FOCUS,
                 id='claude-oversized-launch-file'),
    pytest.param('codex', {'AGENTS.md': PROCEDURE_SECTION}, True, recheck.PROCEDURE, None, id='codex-procedure-section'),
    pytest.param('claude', {'CLAUDE.md': PROCEDURE_SECTION}, True, recheck.PROCEDURE, None, id='claude-procedure-section'),
    pytest.param('codex', {'AGENTS.md': LINE*250}, True, recheck.SOFT_BUDGET, None, id='codex-lines-above-convention'),
    pytest.param('claude', {'CLAUDE.md': LINE*250}, True, recheck.SOFT_BUDGET, None, id='claude-lines-above-convention'),
    pytest.param('codex', {'AGENTS.md': 'short\n', **skills('.agents/skills')}, True, recheck.ROUTING, recheck.ROUTING_FOCUS,
                 id='codex-routing-collision'),
    pytest.param('claude', {'CLAUDE.md': 'short\n', **skills('.claude/skills')}, True, recheck.ROUTING, recheck.ROUTING_FOCUS,
                 id='claude-routing-collision'),
])
def test_each_advertised_convention_is_discovered_with_its_label_and_focus(tmp_path, agent, files, git, reason, focus):
    found = discover(build(tmp_path, files, git=git), agent)
    row = next(row for row in found['candidates'] if row['reason'] == reason)
    assert found['inventory_status'] == 'completed'
    assert (row['hypothesis_source'], row['authority'], row['selection'], row['publication_allowed']) == (
        'static_convention', 'none', recheck.AUTOMATIC, False)
    assert row['focus'] == (focus or improvement_hint.HINTS[reason]) and row['finding_id'] and row['convention_source']


def test_the_chain_skipped_and_declared_budget_reasons_cannot_arise_from_a_project_root_audit_with_the_default_budget(tmp_path):
    found = discover(build(tmp_path, {'AGENTS.override.md': 'y'*40000 + '\n', 'AGENTS.md': 'z'*40000 + '\n'}), 'codex')
    assert {row['reason'] for row in found['candidates']} == {chain.TRUNCATED, 'instruction_exceeds_project_doc_bytes'}


@pytest.mark.parametrize('agent, files, git, reason', [
    pytest.param('codex', {'AGENTS.md': LINE*250}, False, 'project_root_unsettled', id='codex-without-project-root'),
    pytest.param('claude', {'CLAUDE.md': '@missing-import.md\n' + LINE*250}, True, 'import_unresolved', id='claude-unresolved-import'),
])
def test_unsettled_instruction_totals_are_reported_with_their_reasons_not_as_a_clean_result(tmp_path, agent, files, git, reason):
    found = discover(build(tmp_path, files, git=git), agent)
    assert found['candidates'] == [] and found['inventory_status'] == 'completed'
    assert found['instruction_totals']['status'] == 'unknown' and reason in found['instruction_totals']['reasons']
    needing = set(recheck.APPLICABLE[agent]) & recheck.NEEDS_TOTALS
    assert set(found['unsettled']) == needing and not needing & set(found['checked']) and recheck.ROUTING in found['checked']


def test_totals_that_cannot_be_read_are_unsettled_with_a_typed_reason_that_leaks_no_detail(tmp_path, monkeypatch):
    def unreadable(*args, **kwargs): raise OSError('synthetic read failure')
    monkeypatch.setattr(recheck, 'Folder', unreadable)
    found = discover(build(tmp_path, {'CLAUDE.md': LINE*250}), 'claude')
    assert found['candidates'] == [] and found['instruction_totals'] == dict(status='unknown', reasons=['instruction_totals_unavailable'])
    assert set(found['unsettled']) == set(recheck.APPLICABLE['claude']) & recheck.NEEDS_TOTALS and 'synthetic read failure' not in repr(found)


@pytest.mark.parametrize('agent', ['codex', 'claude'])
def test_a_settled_chain_with_nothing_eligible_lists_every_evaluated_convention_as_checked(tmp_path, agent):
    found = discover(build(tmp_path, {INSTRUCTION_FILE[agent]: 'short\n'}), agent)
    assert found['candidates'] == [] and found['unsettled'] == [] and found['instruction_totals']['status'] == 'declared'
    assert set(found['checked']) == set(recheck.APPLICABLE[agent])


def test_an_audit_inventory_that_is_not_completed_evaluated_nothing(tmp_path, monkeypatch):
    blocked = SimpleNamespace(status='blocked', blocked_reason='synthetic_block', findings=(), harness_inventory_sha256='0'*64)
    monkeypatch.setattr(recheck.audit, 'audit', lambda *args, **kwargs: blocked)
    found = discover(build(tmp_path, {'AGENTS.md': 'short\n'}), 'codex')
    assert (found['inventory_status'], found['blocked_reason'], found['candidates'], found['checked']) == ('blocked', 'synthetic_block', [], [])
    assert set(found['unsettled']) == set(recheck.APPLICABLE['codex'])
