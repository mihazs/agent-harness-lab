"""docs/loop-reference.md states the declaration, states, stops, decision inputs and reasons the loop package defines."""
from dataclasses import fields
from pathlib import Path

import pytest

from agent_harness_lab.loop import controller, ledger, machine
from agent_harness_lab.loop.errors import REASONS
from agent_harness_lab.loop.spec import AUTHORITY, DECLARED, DEFAULTS, MAX_ROUNDS_CAP
from tests.support import loop_sim

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


@pytest.mark.parametrize('preset', sorted(loop_sim.PRESETS))
def test_every_simulation_preset_is_named_in_the_reference(preset):
    assert f'`{preset}`' in section('Simulation test-bed')


@pytest.mark.parametrize('name', [*ledger.STATE, 'frozen_inputs', 'evaluate', 'investigate', 'baseline', 'mechanisms', 'propose', 'Evaluation', 'Candidate',
                                  'pending', 'evaluating', 'deferred', *controller.FINAL.values(), controller.RESERVE_UNAVAILABLE, 'unknown_usage_evaluations',
                                  'loop_port_invalid', 'loop_ledger_invalid', 'loop_ledger_full'])
def test_every_controller_event_port_disposition_and_reason_is_named_in_the_controller_section(name):
    assert f'`{name}`' in section('Loop controller')


def test_every_key_of_a_controller_result_is_named_in_the_result_table(tmp_path):
    result, _, _ = loop_sim.drive(loop_sim.converges_in_k_rounds(2), tmp_path)
    table = section('Loop controller').split('### Result')[1].split('###')[0]
    assert [key for key in result if f'`{key}`' not in table] == []
    assert controller.PROFILE in table and '4 * max_rounds + 8' in section('Loop controller')


def test_the_documented_event_cap_exceeds_what_a_run_with_the_most_events_writes(tmp_path):
    script = loop_sim.regresses_on_round_n(3, on_regression='rollback_continue')
    result, _, _ = loop_sim.drive(script, tmp_path)
    assert result['ledger']['events'] <= ledger.event_cap(script.spec)
