"""Instruction ordering is a conditional declaration, never a semantic or loading observation."""
import json

import pytest

from agent_harness_lab import audit


@pytest.fixture
def checkout(tmp_path):
    (tmp_path / '.git').mkdir()
    (tmp_path / 'src').mkdir()
    (tmp_path / 'AGENTS.md').write_text('Use synthetic option A.\n')
    (tmp_path / 'src/AGENTS.md').write_text('Use synthetic option B.\n')
    (tmp_path / 'src/AGENTS.override.md').write_text('Use synthetic option C.\n')
    (tmp_path / 'CLAUDE.md').write_text('Use synthetic option A.\n')
    (tmp_path / 'src/CLAUDE.md').write_text('Use synthetic option B.\n')
    return tmp_path


def test_codex_reports_conditional_override_and_directory_order(checkout):
    result = audit.run(checkout, 'codex', task_cwd='src').document()['instruction_precedence']
    pairs = {(r['higher'], r['lower'], r['rule']) for r in result['conditional_relations']}
    assert ('src/AGENTS.override.md', 'src/AGENTS.md', 'same_directory_selection') in pairs
    assert ('src/AGENTS.override.md', 'AGENTS.md', 'closer_directory_if_both_loaded') in pairs
    assert result['conflict_detection'] == 'not_checked'
    assert result['effective'] == 'unknown'
    assert set(result['unchecked'].values()) == {'not_checked'}
    assert all(r['source'] == result['source'] for r in result['conditional_relations'])
    assert 'synthetic option' not in json.dumps(result)
    assert not result['publication_allowed']


def test_claude_documents_load_order_without_claiming_conflicting_instruction_winner(checkout):
    result = audit.run(checkout, 'claude-code', task_cwd='src').document()['instruction_precedence']
    assert result['documented_load_order'] == ['managed', 'user', 'project', 'local']
    assert {'CLAUDE.md', 'src/CLAUDE.md'} <= {f['path'] for f in result['files']}
    assert result['conditional_relations'] == []
    assert result['effective'] == 'unknown'


def test_refused_override_does_not_create_a_selection_or_order(checkout):
    (checkout / 'src/AGENTS.override.md').unlink()
    (checkout / 'src/AGENTS.override.md').symlink_to(checkout / 'AGENTS.md')
    result = audit.run(checkout, 'codex', task_cwd='src').instruction_precedence
    assert result['conditional_relations'] == []
    assert result['refusals'] == [dict(path='src/AGENTS.override.md', root='checkout', reason='symlink', status='not_checked')]
