"""The line gate of the verified-correction lane (#1145): only the cited lines may be replaced or removed."""
import pytest

from agent_harness_lab.evaluation.verified_correction import only_cited_lines

WHERE = [dict(file='AGENTS.md', line=2)]
BASE = 'one\ntwo\nthree\n'


def gate(tmp_path, candidate, name='AGENTS.md', mode_changed=False, other=None):
    baseline, folder = tmp_path/'baseline', tmp_path/'candidate'
    for root, text in ((baseline, BASE), (folder, candidate)):
        root.mkdir(exist_ok=True)
        (root/'AGENTS.md').write_text(text)
    original, proposed = {'AGENTS.md': ('a', 0o644)}, {'AGENTS.md': ('b', 0o755 if mode_changed else 0o644)}
    if other: original[other], proposed[other] = None, ('c', 0o644)
    return only_cited_lines(baseline, folder, original, proposed, WHERE)


@pytest.mark.parametrize('candidate, allowed', [('one\nTWO\nthree\n', True), ('one\nthree\n', True), ('ONE\ntwo\nthree\n', False),
    ('one\ntwo\nthree\nfour\n', False), ('one\nTWO\nTHREE\n', False), ('one\ntwo\n', False)])
def test_only_a_replaced_or_removed_cited_line_passes(tmp_path, candidate, allowed):
    assert gate(tmp_path, candidate) is allowed


def test_a_mode_change_or_another_file_fails(tmp_path):
    assert not gate(tmp_path, 'one\nTWO\nthree\n', mode_changed=True)
    assert not gate(tmp_path, 'one\nTWO\nthree\n', other='CLAUDE.md')
