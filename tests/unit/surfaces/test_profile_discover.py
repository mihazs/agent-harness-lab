"""A profile finding is a static convention candidate for a first run without outcome evidence (#1119); synthetic only."""
import pytest

from agent_harness_lab.audit import recheck
from agent_harness_lab.audit.recheck import discover
from agent_harness_lab.project_profile import findings
from tests.support.profile_fixture import build, package

NODE = {'package.json': package({'test': 'vitest run'}), 'pnpm-lock.yaml': ''}


def project(tmp_path, files):
    root = build(tmp_path, files)
    (root / '.git').mkdir()
    return root


def reasons(found):
    return [row['reason'] for row in found['candidates']]


def test_a_contradicted_line_is_a_candidate_with_the_contradiction_focus(tmp_path):
    found = discover(project(tmp_path, {**NODE, 'AGENTS.md': 'Use pnpm and `pnpm test`; deploy with `pnpm run deploy`.\n'}), 'codex')
    (row,) = [row for row in found['candidates'] if row['reason'] == 'harness_profile_command_target_missing']
    assert (row['hypothesis_source'], row['authority'], row['selection'], row['publication_allowed']) == ('static_convention', 'none', recheck.AUTOMATIC, False)
    assert row['focus'] == recheck.PROFILE_FOCUS['contradiction'] and row['finding_id'].startswith('hp/command_target_missing/')
    assert found['harness_profile'] == dict(status='compared', findings=1)


def test_contradictions_rank_before_shape_conventions_and_gaps_last(tmp_path):
    text = 'Deploy with `scripts/deploy.sh`.\n' + 'synthetic line\n' * 250
    found = discover(project(tmp_path, {**NODE, 'AGENTS.md': text}), 'codex')
    order = reasons(found)
    assert order.index('harness_profile_path_reference_missing') < order.index(recheck.SOFT_BUDGET) < order.index('harness_profile_toolchain_not_stated')
    assert order[-1].startswith('harness_profile_')


def test_a_clean_project_adds_no_profile_candidate(tmp_path):
    found = discover(project(tmp_path, {**NODE, 'AGENTS.md': 'Use pnpm. Run `pnpm test`; lint with eslint; build with vite.\n'}), 'claude')
    assert not any(reason.startswith('harness_profile_') for reason in reasons(found)) and found['harness_profile']['findings'] == 0


def test_the_candidate_is_deterministic_so_the_baseline_recheck_agrees(tmp_path):
    root = project(tmp_path, {**NODE, 'AGENTS.md': 'Run `pnpm run deploy`.\n'})
    assert discover(root, 'codex')['candidates'] == discover(root, 'codex')['candidates']


def test_every_finding_kind_has_a_priority_slot_and_a_focus():
    assert {reason for reason in recheck.PRIORITY if reason.startswith('harness_profile_')} == {'harness_profile_' + kind for kind in findings.KINDS}
    assert set(recheck.PROFILE_FOCUS) == {'contradiction', 'gap'}


@pytest.mark.parametrize('agent', ['codex', 'claude'])
def test_the_focus_names_no_instruction_text(tmp_path, agent):
    found = discover(project(tmp_path, {**NODE, 'AGENTS.md': 'Run `pnpm run SECRETSCRIPT`.\n', 'CLAUDE.md': '@AGENTS.md\n'}), agent)
    assert 'SECRETSCRIPT' not in repr([(row['focus'], row['reason']) for row in found['candidates']])
