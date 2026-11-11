"""The documented capability matrix must name the pinned constants the adapters declare."""
from pathlib import Path

from agent_harness_lab.telemetry import claude_native, codex_rollout

DOC = (Path(__file__).resolve().parents[3] / 'docs/telemetry.md').read_text()
MATRIX = DOC.split('## Native capture capability matrix', 1)[1].split('\n## ', 1)[0]


def row(name):
    line = next(line for line in MATRIX.splitlines() if line.startswith(f'| {name} |'))
    return [cell.strip() for cell in line.strip('|').split('|')][1:]


def test_pinned_versions_and_commits_match_the_adapters():
    codex, claude = row('Pinned source')
    assert codex_rollout.UPSTREAM_COMMIT in codex and claude_native.UPSTREAM_COMMIT in claude
    header = next(line for line in MATRIX.splitlines() if line.startswith('| Capability'))
    assert codex_rollout.VERSION in header and claude_native.VERSION in header


def test_loading_and_activation_claims_match_declared_capabilities():
    assert codex_rollout.LOADING_SIGNAL in row('Harness loading')[0]
    assert claude_native.CAPABILITY['loading_signal'] in row('Harness loading')[1]
    assert claude_native.CAPABILITY['skill_activation'] is False
    assert all(cell.startswith('unknown') for cell in row('Skill activation'))


def test_cancellation_and_compaction_claims_match_the_mappings():
    assert codex_rollout.ACTIVITIES['turn_aborted'] == 'cancelled' and 'cancelled' in row('Cancellation')[0]
    assert row('Cancellation')[1] == 'not mapped'
    assert 'cancelled' not in open(claude_native.__file__).read()
    assert codex_rollout.ACTIVITIES['context_compacted'] == 'compaction'
