"""The Bayesian decision score pages (explanation, how-to, reference) stay true to the code they document.

Offline and synthetic: the output-block table is compared with blocks the command really produces, the documented commands
are executed and the documented fixture table is compared with the committed expected verdicts.
"""
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys

import pytest

from agent_harness_lab.evaluation.bayes_analysis import attach
from agent_harness_lab.evaluation.statistics import analyze
from tests.support.bayes_records import EXPECTED_VERDICTS, KINDS, bank, expected_verdicts, experiment, run
from tests.support.bayes_scenarios import plan

ROOT = Path(__file__).resolve().parents[3]
DOCS = ROOT / 'docs'
EXPLANATION, HOW_TO, REFERENCE = 'bayesian-decision-score.md', 'bayesian-decision-how-to.md', 'bayesian-decision-reference.md'
NO_PEER_REVIEWED = ('No peer-reviewed work applies a paired Bayesian posterior with a region of practical equivalence '
                    'to agent harness scoring.')
REPORT_UNAVAILABLE = ('The research report this design cites was not available, so every claim that depends on it is '
                      'unverified.')
LIMITS = ('Few independent roots', 'Repeated looks', 'Roots are assumed independent', 'calibrated only under declared '
          'synthetic models', 'recommendation-only')
SOURCES = {'plan', 'trials', 'analysis', 'resource decision', 'constant'}
PRIVATE = re.compile(r'/home/|/Users/|~/|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')


def page(name):
    return (DOCS / name).read_text()


def flat(text):
    return ' '.join(text.split())


def section(text, heading):
    """The lines of one level-2 section, without its heading, as one string."""
    kept, taking = [], False
    for line in text.splitlines():
        if line.startswith('## '):
            taking = line == f'## {heading}'
        elif taking:
            kept.append(line)
    assert kept, f'no section {heading!r}'
    return '\n'.join(kept)


def rows(text):
    """The cells of every table row of ``text``, header and rule rows excluded."""
    table = [[cell.strip() for cell in line.strip().strip('|').split('|')] for line in text.splitlines() if line.startswith('|')]
    return [cells for cells in table[1:] if not set(''.join(cells)) <= set('-: ')]


def names(cell):
    return re.findall(r'`([^`]+)`', cell)


def fences(text, language):
    return re.findall(rf'```{language}\n(.*?)```', text, re.S)


def key_paths(value, path=''):
    """Every key path of a block; a family key is written <family> and a list element []."""
    if path:
        yield path
    if isinstance(value, dict):
        for key, item in value.items():
            yield from key_paths(item, f'{path}.{"<family>" if path == "families" else key}' if path else key)
    elif isinstance(value, list):
        for item in value:
            yield from key_paths(item, path + '[]')


def produced(tmp_path):
    """One block per fixture through the command, plus one with an evaluated resource gate through the library."""
    blocks = {}
    for kind in KINDS:
        directory = tmp_path / kind
        directory.mkdir()
        case = bank(kind)
        code, out, err = experiment(directory, case['spec'], case['records'])
        assert err == '' and code == 3
        blocks[kind] = json.loads(out)['bayesian_decision']
    case = bank('clear_win', resource_contract_sha256='a' * 64)
    blocks['resource'] = attach(case['spec'], case['records'], analyze(case['spec'], case['records']),
                                resource_decision=dict(decision='noninferior', contract_sha256='a' * 64))['bayesian_decision']
    return blocks


def test_each_page_exists_and_is_indexed_under_its_diataxis_purpose():
    index = page('README.md')
    for heading, name in (('Complete a task', HOW_TO), ('Look up behavior and contracts', REFERENCE),
                          ('Understand the context', EXPLANATION)):
        assert f']({name}' in section(index, heading), (heading, name)
    for name, others in ((EXPLANATION, (HOW_TO, REFERENCE)), (HOW_TO, (EXPLANATION, REFERENCE)), (REFERENCE, (EXPLANATION, HOW_TO))):
        assert all(f']({other}' in page(name) for other in others), name


@pytest.mark.parametrize('name', (EXPLANATION, HOW_TO, REFERENCE))
def test_each_page_states_the_evidence_status_and_the_missing_report(name):
    text = flat(page(name))
    assert NO_PEER_REVIEWED in text and REPORT_UNAVAILABLE in text and 'inference by analogy' in text


def test_the_explanation_and_the_how_to_state_the_limits():
    for name, text in ((EXPLANATION, section(page(EXPLANATION), 'Limits')), (HOW_TO, section(page(HOW_TO), 'Limits'))):
        assert all(limit in flat(text) for limit in LIMITS), (name, [limit for limit in LIMITS if limit not in flat(text)])
    assert 'bayesian-decision-score.md#limits' in page(REFERENCE)


def test_the_output_block_table_matches_the_produced_blocks_in_both_directions(tmp_path):
    documented = {name for cells in rows(section(page(REFERENCE), 'Output block')) for name in names(cells[0])}
    blocks = produced(tmp_path)
    union = {path for block in blocks.values() for path in key_paths(block)}
    assert union - documented == set() and documented - union == set()
    top = {name for name in documented if '.' not in name and '[' not in name}
    assert all(set(block) == top for block in blocks.values())


def test_every_output_field_says_where_its_number_comes_from():
    table = rows(section(page(REFERENCE), 'Output block'))
    assert table and all(len(cells) == 3 for cells in table)
    for cells in table:
        assert {word.strip() for word in cells[2].split(',')} <= SOURCES, cells


def test_the_fixture_table_matches_the_committed_expected_verdicts():
    committed = expected_verdicts()
    table = {names(cells[0])[0]: [names(cell)[0] for cell in cells[1:5]] for cells in rows(section(page(REFERENCE), 'Fixtures'))}
    assert set(table) == set(committed) == set(KINDS)
    assert all(table[kind] == [row['verdict'], row['reason'], row['stage'], row['analysis']] for kind, row in committed.items())
    short = {names(cells[0])[0]: [names(cell)[0] for cell in cells[1:3]] for cells in rows(section(page(HOW_TO), 'Reproduce the six examples'))}
    assert short == {kind: [row['verdict'], row['reason']] for kind, row in committed.items()}
    reasons = {name for cells in rows(section(page(REFERENCE), 'Reasons')) for name in names(cells[0])}
    assert {row['reason'] for row in committed.values()} <= reasons


def test_the_verdict_table_lists_the_five_verdicts_and_none_has_authority():
    table = {names(cells[0])[0]: cells for cells in rows(section(page(REFERENCE), 'Verdict meanings'))}
    assert set(table) == {'better', 'equivalent', 'worse', 'insufficient_evidence', 'blocked'}
    assert all(cells[2].startswith('none') for cells in table.values())


def test_the_documented_declarations_are_accepted_and_the_defaults_resolve_as_documented():
    minimal, resolved = (json.loads(block) for block in fences(section(page(HOW_TO), 'Declare the score in a plan'), 'json'))
    assert set(minimal) == {'schema_version', 'profile'}
    for declared in (minimal, resolved):
        spec = plan(5, bayes=declared, draws=20000)  # effect_margin .1 and family_margin .05, the values the defaults follow
        spec.validate()
        assert spec.bayes_decision == resolved


def test_the_documented_commands_reproduce_the_documented_fixtures(tmp_path):
    committed = expected_verdicts()
    lines = [line for block in fences(section(page(HOW_TO), 'Reproduce the six examples'), 'sh') for line in block.splitlines()
             if line.strip() and not line.startswith('#')]
    build = [line for line in lines if 'build_bayes_fixtures.py' in line]
    commands = [line for line in lines if line.startswith('ahl ')]
    assert len(build) == 1 and len(commands) == 1
    out = tmp_path / 'fixtures'
    argv = shlex.split(build[0].replace('/tmp/ahl-bayes-fixtures', str(out)))
    assert argv[0] in ('python3', '.venv/bin/python')
    result = subprocess.run([sys.executable, *argv[1:]], capture_output=True, text=True, cwd=ROOT, timeout=120)
    assert result.returncode == 0, result.stderr
    for kind, row in committed.items():
        argv = shlex.split(commands[0].replace('/tmp/ahl-bayes-fixtures', str(out)).replace('clear_win', kind))
        code, stdout, err = run(*argv[1:])
        block = json.loads(stdout)['bayesian_decision']
        assert (code, err) == (3, '') and (block['verdict'], block['reason'], block['stage']) == (
            row['verdict'], row['reason'], row['stage'])


def test_the_abridged_block_in_the_how_to_equals_the_block_the_command_prints(tmp_path):
    shown = json.loads(fences(section(page(HOW_TO), 'Read the block'), 'json')[0])
    case = bank('clear_win')
    code, out, err = experiment(tmp_path, case['spec'], case['records'])

    def holds(documented, actual):
        if isinstance(documented, dict):
            return isinstance(actual, dict) and all(key in actual and holds(item, actual[key]) for key, item in documented.items())
        return documented == actual

    assert (code, err) == (3, '') and holds(shown, json.loads(out)['bayesian_decision'])


def test_the_pages_and_their_inputs_carry_no_private_path_or_identifier():
    for path in (*(DOCS / name for name in (EXPLANATION, HOW_TO, REFERENCE)), EXPECTED_VERDICTS, ROOT / 'scripts' / 'build_bayes_fixtures.py'):
        assert PRIVATE.search(path.read_text()) is None, path
