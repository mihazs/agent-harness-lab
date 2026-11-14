"""docs/loop-reference.md states the declaration, states, stops, decision inputs and reasons the loop package defines."""
from dataclasses import fields
from pathlib import Path

import pytest

from agent_harness_lab.loop import machine
from agent_harness_lab.loop.errors import REASONS
from agent_harness_lab.loop.spec import AUTHORITY, DECLARED, DEFAULTS, MAX_ROUNDS_CAP

ROOT = Path(__file__).resolve().parents[3]
REFERENCE = (ROOT/'docs/loop-reference.md').read_text()


def section(heading):
    return REFERENCE.split(f'## {heading}')[1].split('\n## ')[0]


def row(name, heading='Declaration fields'):
    return next(line for line in section(heading).splitlines() if line.startswith(f'| `{name}` |'))


def test_documentation_index_links_the_loop_reference():
    assert '(loop-reference.md)' in (ROOT/'docs/README.md').read_text()


@pytest.mark.parametrize('field', DECLARED)
def test_each_declaration_field_has_one_row_with_its_default(field):
    assert sum(line.startswith(f'| `{field}` |') for line in section('Declaration fields').splitlines()) == 1
    default = DEFAULTS[field]
    if default is not None: assert f'`{default}`' in row(field)
    else: assert 'none' in row(field)


def test_the_round_cap_is_documented():
    assert f'the hard cap is {MAX_ROUNDS_CAP}' in REFERENCE and f'from 1 to {MAX_ROUNDS_CAP}' in REFERENCE


@pytest.mark.parametrize('name', [*machine.STATES, *machine.STOPS, *machine.OUTCOMES, *sorted(AUTHORITY), *(f.name for f in fields(machine.Inputs)),
                                  'relative_supported_improvement', 'acceptance_source: default', 'loop_order_violation'])
def test_every_state_stop_outcome_authority_key_and_decision_input_is_named(name):
    assert f'`{name.split(":")[0]}`' in REFERENCE


@pytest.mark.parametrize('reason', sorted(REASONS))
def test_every_loop_reason_is_listed_once_in_the_reference_table(reason):
    assert sum(line.startswith(f'| `{reason}` |') for line in section('Typed reasons').splitlines()) == 1


@pytest.mark.parametrize('stop', machine.STOPS)
def test_each_stop_criterion_row_lists_exactly_the_states_it_may_be_recorded_from(stop):
    cell = row(stop, 'Outcomes and stop criteria').split('|')[2]
    if len(machine.STOP_FROM[stop]) == len(machine.LIVE): assert 'any state before `stopped`' in cell
    else: assert sorted(state for state in machine.STATES if f'`{state}`' in cell) == sorted(machine.STOP_FROM[stop])
