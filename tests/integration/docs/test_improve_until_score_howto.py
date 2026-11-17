"""Run every command of docs/improve-until-score.md against a synthetic project and a stub agent (#817).

Each shell block of the how-to is one `ahl improve .` command and a trailing comment naming the outcome the page documents:
`# stops: CRITERION (exit N)`, `# loop: not_used (exit N)` or `# resumes: ... (exit N)`. The test runs the command on a project built to
produce that outcome (a pinned copy of the starter bank, five scored roots, and a stub agent whose trials depend only on the harness)
and compares the recorded stop criterion and exit code with the page. No real project, session or model is used.
"""
from contextlib import ExitStack
import re
import shlex
from pathlib import Path

import pytest

from agent_harness_lab.loop import machine
from tests.support import loop_improve as li

ROOT = Path(__file__).resolve().parents[3]
HOWTO = (ROOT/'docs/improve-until-score.md').read_text()
BLOCKS = [block.splitlines() for block in re.findall(r'```sh\n(.*?)\n```', HOWTO, re.S)]
SINGLE_PASS = dict(baseline='solves=responses', script={0: dict(harness=li.EVERYTHING)})  # the baseline the single pass needs to recommend
SYNTHETIC = dict(script={0: dict(harness=li.EVERYTHING)}, loop=dict(alpha=0.999))
PROJECTS = {
    'acceptance_score_reached': SYNTHETIC, 'max_rounds': SYNTHETIC, 'resumes': SYNTHETIC,
    'accepted_without_change': dict(baseline=li.EVERYTHING, loop=dict(alpha=0.999)), 'not_used': SINGLE_PASS,
}
OUTCOME = re.compile(r'# (stops|loop|resumes): ([a-z_ ]+?|[^(]+?) \(exit (\d)\)')


@pytest.fixture
def stack():
    with ExitStack() as stack:
        yield stack


def parse(lines):
    command, outcome = lines
    kind, what, code = OUTCOME.fullmatch(outcome).groups()
    arguments = shlex.split(command)
    assert arguments[:3] == ['ahl', 'improve', '.'], command
    return arguments[3:], kind, what, int(code)


def test_the_how_to_documents_every_stop_outcome_it_runs_and_each_block_is_one_command_and_one_outcome():
    assert len(BLOCKS) >= 6 and all(len(lines) == 2 and OUTCOME.fullmatch(lines[1]) for lines in BLOCKS), BLOCKS
    stops = {parse(lines)[2] for lines in BLOCKS if parse(lines)[1] == 'stops'}
    assert stops <= set(machine.STOPS) and {'acceptance_score_reached', 'max_rounds', 'accepted_without_change'} <= stops


@pytest.mark.parametrize('lines', BLOCKS, ids=[' '.join(lines[0].split()[3:]) or 'defaults' for lines in BLOCKS])
def test_each_documented_command_ends_with_the_outcome_the_page_states(stack, monkeypatch, lines):
    arguments, kind, what, expected = parse(lines)
    key = what if kind in ('stops', 'loop') else kind
    project = li.LoopProject(stack, monkeypatch, **PROJECTS[key])
    if kind == 'resumes':
        _, first = project.run('--accept-score', '0.1')
        arguments = [argument if argument != 'RUN_ID' else Path(first['output']).name for argument in arguments]
    code, found = project.run(*arguments)
    assert code == expected, (lines, found.get('status'), found.get('reason'))
    if kind == 'stops':
        assert found['loop']['stop_criterion'] == what and found['reason'] == f'loop_{what}'
        assert (found['loop']['recommendation_only'], found['loop']['automatic_promotion'], found['loop']['certification']) == (True, False, 'unverified')
    elif kind == 'loop':
        assert (found['loop']['status'], found['loop']['deprecated']) == (what, True) and 'stop_criterion' not in found['loop']
    else:
        assert found['loop']['stop_criterion'] == 'acceptance_score_reached' and Path(found['output']).name == arguments[-1]


def test_the_default_command_without_a_score_labels_its_bar_as_the_default():
    default = next(lines for lines in BLOCKS if parse(lines)[0] == [])
    assert parse(default)[2] == 'acceptance_score_reached' and '`loop.acceptance_source` is `default`' in ' '.join(HOWTO.split())


def test_the_how_to_table_lists_every_stop_criterion_exactly_once_with_the_exit_code_the_receipt_maps_it_to():
    from agent_harness_lab.loop import receipt
    table = HOWTO.split('| Exit | Status |', 1)[1].split('\n\n', 1)[0]
    seen = []
    for row in table.splitlines()[2:]:
        code, status, stops, _ = (cell.strip() for cell in row.strip('|').split('|', 3))
        for criterion in re.findall(r'`([a-z_]+)`', stops):
            seen.append(criterion)
            assert receipt.status_of(criterion) == status.strip('`') and receipt.EXIT[status.strip('`')] == int(code)
    assert sorted(seen) == sorted(machine.STOPS)
