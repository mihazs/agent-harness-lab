"""docs/area-reference.md states the registry limits, typed reasons, evidence states and receipt keys the area code defines (#800).

The limits, reasons, states and keys are derived from the code, so a renamed key, a changed limit or a new state breaks this
test until the page says it. Everything here is synthetic.
"""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import re

import pytest

from agent_harness_lab import cli
from agent_harness_lab.evaluation import area_gate, areas, manifest_contract, score
from agent_harness_lab.evaluation.manifest_contract import ContractError
from tests.support import score_fixture as fixture

ROOT = Path(__file__).resolve().parents[3]
REFERENCE = (ROOT / 'docs/area-reference.md').read_text()


def section(heading):
    return REFERENCE.split(f'\n## {heading}', 1)[1].split('\n## ', 1)[0]


def table(text, header):
    """The rows of the table that starts with the given header cell, by the backticked name in their first cell."""
    body = text.split(f'| {header} |', 1)[1].split('\n\n', 1)[0]
    return {line.split('|')[1].strip().strip('`'): line for line in body.splitlines() if line.startswith('| `')}


def documented_keys(text):
    """Every backticked name in the first cell of every table row of the section."""
    return {name for line in text.splitlines() if line.startswith('| `') for name in re.findall(r'`([^`]+)`', line.split('|')[1])}


def test_the_documentation_index_links_the_reference():
    assert '(area-reference.md)' in (ROOT / 'docs/README.md').read_text()


@pytest.mark.parametrize(('field', 'limit'), [('areas', areas.MAX_AREAS), ('areas[].id', areas.MAX_ID), ('areas[].label', areas.MAX_LABEL),
                                              ('areas[].description', areas.MAX_DESCRIPTION)])
def test_each_registry_limit_is_the_one_the_code_enforces(field, limit):
    lines = [line for line in section('Registry').splitlines() if line.startswith(f'| `{field}` | `ahl.json` |')]
    assert len(lines) == 1 and re.search(rf'\b{limit}\b', lines[0])


def test_the_per_task_limit_and_the_suggested_ids_are_the_ones_the_code_defines():
    text = section('Registry')
    assert areas.MAX_PER_TASK == 3 and 'one to three distinct' in text
    assert all(f"`{entry['id']}`" in text for entry in areas.SUGGESTED)


def test_the_documented_area_reasons_are_exactly_the_ones_the_contract_declares_for_registry_and_selection():
    documented = set(table(section('Registry'), 'Reason'))
    assert documented == {reason for reason in manifest_contract.DECLARATION_REASONS if 'area' in reason}


def raised(call, *args, **kwargs):
    with pytest.raises(ContractError) as caught:
        call(*args, **kwargs)
    return caught.value


@pytest.mark.parametrize('failure', [
    lambda: raised(areas.parse_registry, 'not a list'),
    lambda: raised(areas.parse_registry, [dict(id='a', label='A', description='d')] * 2),
    lambda: raised(areas.task_areas, []),
    lambda: raised(areas.task_areas, ['a', 'b', 'c', 'd']),
    lambda: raised(areas.task_areas, 'a'),
    lambda: raised(areas.task_areas, ['a', 'a']),
    lambda: raised(areas.check_declared, ['a'], ['b']),
    lambda: raised(areas.check_selection, ['a'], ['b'], stage='score_selection'),
    lambda: raised(areas.check_selection, ['a'], ['b'], stage='improve_selection'),
    lambda: raised(areas.check_selection, ['a', 'a'], ['a'], stage='score_selection'),
    lambda: raised(areas.select_tasks, [], ['a'], ['a']),
], ids=['registry-shape', 'registry-duplicate', 'task-empty', 'task-too-many', 'task-not-a-list', 'task-duplicate', 'task-undeclared',
        'score-undeclared', 'improve-undeclared', 'selection-duplicate', 'selection-empty'])
def test_every_reason_lists_in_the_page_the_stage_the_code_raises_it_at(failure):
    error = failure()
    assert f'`{error.stage}`' in table(section('Registry'), 'Reason')[error.reason].split('|')[3]


@pytest.fixture(scope='module')
def report(tmp_path_factory):
    root, spec, records = fixture.four_states(tmp_path_factory.mktemp('states') / 'project')
    out = io.StringIO()
    with redirect_stdout(out):
        assert cli.main(['score', str(root), '--spec', str(spec), '--records', str(records)]) == 0
    return json.loads(out.getvalue())


def test_every_state_and_reason_the_score_report_shows_is_in_the_states_table(report):
    states = table(section('Evidence states'), 'State')
    found = {row['state']: row['reason'] for row in report['areas']}
    assert set(found) == set(states) == {'scored', 'provisional', 'no_evidence', 'blocked'}
    for state, reason in found.items():
        assert (reason is None and '| none |' in states[state]) or f'`{reason}`' in states[state].split('|')[3], (state, reason)


def pair(task, baseline, candidate):
    return dict(task=task, baseline=baseline, candidate=candidate, areas=['coding'])


@pytest.mark.parametrize(('pairs', 'state', 'reason'), [
    ([pair('a', True, True), pair('b', False, True)], 'scored', None),
    ([pair('a', True, True), pair('b', None, True)], 'provisional', 'unknown_outcomes'),
    ([pair('a', True, False)], 'provisional', 'below_minimum_roots'),
    ([], 'no_evidence', 'no_tagged_pair'),
    ([pair('a', None, None)], 'no_evidence', 'no_known_outcome'),
], ids=['scored', 'unknown', 'one-root', 'no-pair', 'no-known'])
def test_each_state_and_reason_the_gate_returns_is_in_the_gate_column_of_the_states_table(pairs, state, reason):
    row = area_gate.gate(pairs, ['coding'])['areas'][0]
    assert (row['state'], row['reason']) == (state, reason)
    cells = table(section('Evidence states'), 'State')[state].split('|')
    assert reason is None or f'`{reason}`' in cells[4], reason


def test_every_key_of_the_score_report_its_rows_and_their_arms_is_documented(report):
    text = section('Receipt keys')
    covered = documented_keys(text)
    coding = report['areas'][0]
    assert set(report) <= covered and set(coding) <= covered
    assert all(f'`{key}`' in text for key in (*coding['success']['baseline'], *coding['paired'], *report['experiment'], *report['trials']))


def test_every_key_of_the_gate_block_and_its_rows_is_documented():
    block = area_gate.gate([pair('a', True, True), pair('b', False, True)], ['coding'])
    covered = documented_keys(section('Receipt keys'))
    assert set(block) <= covered and set(block['areas'][0]) <= covered


def test_the_two_profiles_and_the_conditions_of_the_optional_keys_are_stated(report):
    text = section('Receipt keys')
    assert report['overall'] is None and report['overall_withheld'] == 'not_every_area_scored' and report['multiplicity'] is None
    assert score.PROFILE in text and area_gate.PROFILE in text
    assert 'Why `overall` is `null`' in text and 'when `--area` narrows the report' in text
