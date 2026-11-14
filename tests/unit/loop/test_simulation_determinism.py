"""The simulation test-bed is deterministic and reaches no model, subprocess, sleep, clock or network."""
import ast
import json
from pathlib import Path
import socket
import subprocess
import time

import pytest

from tests.support import loop_sim

TRACES = [pytest.param(preset, id=name) for name, preset in loop_sim.PRESETS.items()]


@pytest.mark.usefixtures('sealed')
@pytest.mark.parametrize('preset', TRACES)
def test_two_replays_of_a_script_are_byte_identical_and_reach_no_ambient_effect(preset):
    first, second = loop_sim.canonical(loop_sim.play(preset())), loop_sim.canonical(loop_sim.play(preset()))
    assert first == second and first.encode() == second.encode()


@pytest.mark.parametrize('seed', range(3))
def test_the_same_seed_gives_the_same_trace_on_a_script_with_seeded_noise(seed):
    assert loop_sim.canonical(loop_sim.play(loop_sim.noisy_tie(seed))) == loop_sim.canonical(loop_sim.play(loop_sim.noisy_tie(seed)))


def test_different_seeds_give_different_traces_with_different_noise_and_frozen_inputs():
    first, second = loop_sim.play(loop_sim.noisy_tie(0)), loop_sim.play(loop_sim.noisy_tie(1))
    assert first['frozen_inputs'] != second['frozen_inputs'] and first['events'] != second['events']
    assert [e['score'] for e in first['events'] if 'score' in e] != [e['score'] for e in second['events'] if 'score' in e]


def test_a_canonical_trace_is_sorted_compact_json_that_replays_through_the_machine():
    script = loop_sim.converges_in_k_rounds(2)
    text = loop_sim.canonical(loop_sim.play(script))
    assert text == loop_sim.canonical(json.loads(text)) and ': ' not in text and text.startswith('{"best":')
    assert loop_sim.replay(script.spec, text).stop == 'acceptance_score_reached'


def test_a_trace_replays_only_under_the_declaration_it_was_recorded_with():
    text = loop_sim.canonical(loop_sim.play(loop_sim.converges_in_k_rounds(2)))
    other = loop_sim.regresses_on_round_n(1).spec
    with pytest.raises(ValueError, match='different declaration'):
        loop_sim.replay(other, text)


GUARDED = [
    pytest.param(lambda: time.sleep(0), id='sleep'),
    pytest.param(lambda: time.time(), id='wall-clock'),
    pytest.param(lambda: time.monotonic(), id='monotonic-clock'),
    pytest.param(lambda: subprocess.run(['true']), id='subprocess'),
    pytest.param(lambda: socket.create_connection(('127.0.0.1', 9)), id='network-connection'),
    pytest.param(lambda: socket.socket().connect(('127.0.0.1', 9)), id='socket-connect'),
    pytest.param(lambda: socket.getaddrinfo('localhost', 80), id='name-lookup'),
]


@pytest.mark.usefixtures('sealed')
@pytest.mark.parametrize('reach', GUARDED)
def test_the_guard_fixture_fails_a_test_that_reaches_an_ambient_effect(reach):
    with pytest.raises(AssertionError, match='the simulation reached'):
        reach()


def test_the_test_bed_imports_nothing_from_the_workflow_or_outside_the_loop_package():
    tree = ast.parse(Path(loop_sim.__file__).read_text())
    imported = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module]
    imported += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    assert [name for name in imported if name.startswith('agent_harness_lab')] == ['agent_harness_lab.loop', 'agent_harness_lab.loop.machine', 'agent_harness_lab.loop.spec']
    assert not {'subprocess', 'socket', 'time', 'requests', 'urllib'} & {name.split('.')[0] for name in imported}
