"""A pure Claude trial must read the AGENTS.md of the harness under test (Claude Code loads CLAUDE.md, not AGENTS.md)."""
from agent_harness_lab.evaluation.trial_surfaces import bridge_agents_md


def test_every_agents_md_without_a_claude_md_gets_an_import_and_an_existing_claude_md_is_kept(tmp_path):
    # Regression (fixed-in: 0.233.2): pure Claude trials ignored AGENTS.md, so both arms ran without the instructions under
    # test and every comparison of a project that keeps its instructions in AGENTS.md was a tie by construction.
    (tmp_path / 'AGENTS.md').write_text('root rules\n')
    (tmp_path / 'app').mkdir(); (tmp_path / 'app/AGENTS.md').write_text('app rules\n')
    (tmp_path / 'lib').mkdir(); (tmp_path / 'lib/AGENTS.md').write_text('lib rules\n'); (tmp_path / 'lib/CLAUDE.md').write_text('own\n')
    (tmp_path / '.git').mkdir(); (tmp_path / '.git/AGENTS.md').write_text('not a harness file\n')
    assert bridge_agents_md(tmp_path) == ['CLAUDE.md', 'app/CLAUDE.md']
    assert (tmp_path / 'CLAUDE.md').read_text() == '@AGENTS.md\n' and (tmp_path / 'lib/CLAUDE.md').read_text() == 'own\n'
    assert not (tmp_path / '.git/CLAUDE.md').exists()
    assert bridge_agents_md(tmp_path) == []  # idempotent
