"""The loop ledger on disk: byte-identical runs, owner-only atomic appends, the event cap, interruption and a project left untouched."""
import hashlib
import os
from dataclasses import replace
from pathlib import Path
import socket
import stat
import subprocess
import sys

import pytest

from agent_harness_lab.loop.errors import LoopError
from agent_harness_lab.loop.ledger import NEXT, LoopLedger, event_cap
from agent_harness_lab.loop.machine import STATES
from tests.support import loop_sim

ROOT = Path(__file__).resolve().parents[3]
NOMINAL = [name for name in loop_sim.PRESETS if name != 'interrupted_after_each_event']
HARNESS = {'AGENTS.md': 'Run the tests.\n', 'skills/demo/SKILL.md': '---\nname: demo\n---\nDo it.\n', '.claude/settings.json': '{}\n'}


def project_with_harness(base):
    project = base/'project'
    for name, text in HARNESS.items():
        (project/name).parent.mkdir(parents=True, exist_ok=True)
        (project/name).write_text(text)
    return project


def project_state(project):
    """Every directory and every file hash under the project, so a created, removed or edited entry shows."""
    return sorted((path.relative_to(project).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else 'dir')
                  for path in project.rglob('*'))


def store_files(store):
    """Relative path to bytes for every file in the store, with the random case directory name replaced."""
    case = next((store/'cases').iterdir()).name
    return {path.relative_to(store).as_posix().replace(case, 'case'): path.read_bytes() for path in sorted(store.rglob('*')) if path.is_file()}


def modes(store):
    return {path.relative_to(store).as_posix(): stat.S_IMODE(path.stat().st_mode) for path in [store, *store.rglob('*')]}


class CrashingLedger(LoopLedger):
    """Fails mid-publish, after the event is written to its temporary file and before it is renamed, on the nth write."""
    def __init__(self, root, project, at, error=OSError):
        super().__init__(root, project)
        self.at, self.error = at, error

    def write(self, kind, payload, **options):
        def crash(): raise self.error('crash while publishing')
        crashing = self.count == self.at
        if crashing: self.at = None  # once: a retry, such as the interruption record, goes through
        super().write(kind, payload, **{**options, 'before_publish': crash if crashing else options.get('before_publish')})


def uninterrupted(script, base):
    """The events of the same script run to its end; an interrupted run must match this up to its interruption."""
    _, ledger, _ = loop_sim.drive(replace(script, interrupt_after=None), base / 'uninterrupted')
    return ledger.events()


@pytest.mark.parametrize('preset', NOMINAL)
def test_two_runs_of_one_script_write_byte_identical_ledgers(tmp_path, preset):
    first, _, _ = loop_sim.drive(loop_sim.PRESETS[preset](), tmp_path / 'one')
    second, _, _ = loop_sim.drive(loop_sim.PRESETS[preset](), tmp_path / 'two')
    one, two = store_files(tmp_path/'one'/'store'), store_files(tmp_path/'two'/'store')
    assert one == two and first == second and any(name.endswith('events/0000.json') for name in one)


def test_a_ledger_is_owner_only_with_one_file_per_event_and_the_frozen_spec(tmp_path):
    result, ledger, _ = loop_sim.drive(loop_sim.converges_in_k_rounds(2), tmp_path)
    store = tmp_path/'store'
    assert {name: oct(mode) for name, mode in modes(store).items() if mode != (0o700 if (store/name).is_dir() else 0o600)} == {}
    events = sorted((ledger.case_dir/'events').iterdir())
    assert [path.name for path in events] == [f'{index:04d}.json' for index in range(result['ledger']['events'])] and (ledger.case_dir/'spec.json').is_file()


def test_the_result_names_the_ledger_head_so_two_runs_agree_on_it(tmp_path):
    first, ledger, _ = loop_sim.drive(loop_sim.converges_in_k_rounds(2), tmp_path / 'one')
    second, _, _ = loop_sim.drive(loop_sim.converges_in_k_rounds(2), tmp_path / 'two')
    assert first['ledger'] == second['ledger'] == dict(events=11, head_sha256=ledger.head)


def test_event_times_come_from_the_injected_clock_not_the_wall_clock(tmp_path):
    _, ledger, _ = loop_sim.drive(loop_sim.converges_in_k_rounds(1), tmp_path)
    stamps = [path.read_text() for path in sorted((ledger.case_dir/'events').iterdir())]
    assert '"recorded_at": "0.000000"' in stamps[0] and '"recorded_at": "20.000000"' in stamps[-1]


@pytest.mark.parametrize('preset', NOMINAL)
def test_a_run_leaves_the_projects_harness_files_and_directories_untouched(tmp_path, preset):
    project = project_with_harness(tmp_path)
    before = project_state(project)
    loop_sim.drive(loop_sim.PRESETS[preset](), tmp_path)
    assert project_state(project) == before and not (tmp_path/'store').is_relative_to(project)
    assert {name for name, _ in before} >= set(HARNESS)


def test_an_interrupted_run_also_leaves_the_project_untouched(tmp_path):
    project = project_with_harness(tmp_path)
    before = project_state(project)
    result, _, _ = loop_sim.drive(loop_sim.interrupted_after_each_event(4), tmp_path)
    assert (result['stop'], project_state(project)) == ('interrupted', before)


def test_the_ledger_hands_back_the_events_it_recorded_in_order(tmp_path):
    result, ledger, _ = loop_sim.drive(loop_sim.converges_in_k_rounds(2), tmp_path)
    kinds = [kind for kind, _ in ledger.events()]
    assert kinds == ['spec_frozen', 'baseline_evaluated', *['round_started', 'candidate_frozen', 'candidate_evaluated', 'decided']*2, 'stopped']
    assert result['ledger']['events'] == len(kinds) <= event_cap(loop_sim.converges_in_k_rounds(2).spec)


def started(tmp_path, max_rounds=1):
    (tmp_path/'project').mkdir(exist_ok=True)
    spec = loop_sim.build('events', [0.5], declaration=dict(max_rounds=max_rounds, plateau_rounds=1)).spec
    return LoopLedger(tmp_path/'store', tmp_path/'project').begin(spec), spec


@pytest.mark.parametrize('max_rounds', [1, 2, 10])
def test_the_ledger_accepts_its_bounded_events_and_then_rejects_with_a_typed_error(tmp_path, max_rounds):
    ledger, spec = started(tmp_path, max_rounds)
    cap = 5*max_rounds + 9
    sequence = (['spec_frozen', 'baseline_evaluated'] + ['proposal_started', 'round_started', 'candidate_frozen', 'candidate_evaluated', 'decided']*(max_rounds + 2))[:cap]
    for kind in sequence: ledger.write(kind, {}, elapsed=0.0)
    assert (event_cap(spec), len(sequence), ledger.count) == (cap, cap, cap)
    before = (ledger.head, sorted(path.name for path in (ledger.case_dir/'events').iterdir()))
    with pytest.raises(LoopError) as caught:
        ledger.write(NEXT[sequence[-1]][0], {}, elapsed=0.0)
    assert (caught.value.reason, caught.value.field) == ('loop_ledger_full', 'events')
    assert (ledger.head, sorted(path.name for path in (ledger.case_dir/'events').iterdir())) == before


@pytest.mark.parametrize('kinds, bad', [
    pytest.param([], 'baseline_evaluated', id='first-event-is-not-spec-frozen'),
    pytest.param(['spec_frozen'], 'round_started', id='round-before-the-baseline'),
    pytest.param(['spec_frozen', 'baseline_evaluated'], 'decided', id='decision-before-a-candidate'),
    pytest.param(['spec_frozen', 'baseline_evaluated', 'stopped'], 'round_started', id='event-after-the-stop'),
    pytest.param(['spec_frozen'], 'spec_frozen', id='spec-frozen-twice'),
    pytest.param(['spec_frozen'], 'not_an_event', id='unknown-kind'),
])
def test_the_ledger_refuses_an_event_out_of_order_with_a_typed_error_and_writes_nothing(tmp_path, kinds, bad):
    ledger, _ = started(tmp_path)
    for kind in kinds: ledger.write(kind, {}, elapsed=0.0)
    with pytest.raises(LoopError) as caught:
        ledger.write(bad, {}, elapsed=0.0)
    assert caught.value.reason == 'loop_ledger_invalid' and ledger.count == len(kinds)
    assert len(list((ledger.case_dir/'events').iterdir())) == len(kinds)


def test_an_append_that_crashes_before_it_is_published_leaves_no_event_and_the_next_append_continues_the_chain(tmp_path):
    ledger, spec = started(tmp_path)
    ledger.write('spec_frozen', {'spec_sha256': spec.digest}, elapsed=0.0)
    head = ledger.head

    def crash(): raise OSError('power loss')
    with pytest.raises(OSError, match='power loss'):
        ledger.write('baseline_evaluated', dict(value='valid'), elapsed=1.0, before_publish=crash)
    assert (ledger.count, ledger.head) == (1, head) and sorted(path.name for path in (ledger.case_dir/'events').iterdir()) == ['0000.json']
    ledger.write('baseline_evaluated', dict(value='valid'), elapsed=1.0)
    assert [kind for kind, _ in ledger.events()] == ['spec_frozen', 'baseline_evaluated'] and ledger.replay(spec).state == 'baseline_evaluated'


@pytest.mark.parametrize('error', [OSError, RuntimeError])
@pytest.mark.parametrize('at', range(11))
def test_a_crash_at_any_append_leaves_a_ledger_that_replays_through_the_state_machine(tmp_path, at, error):
    script = loop_sim.converges_in_k_rounds(2)
    (tmp_path/'project').mkdir()
    ledger = CrashingLedger(tmp_path/'store', tmp_path/'project', at, error)
    with pytest.raises(error):
        loop_sim.drive(script, tmp_path, ledger=ledger)
    assert ledger.count == at and sorted(path.name for path in (ledger.case_dir/'events').iterdir()) == [f'{index:04d}.json' for index in range(at)]
    assert not [path for path in ledger.case_dir.rglob('*') if path.name.startswith('.')]
    if at: assert ledger.replay(script.spec).state in STATES and len(ledger.events()) == at


def test_a_crashed_ledger_is_not_continued_by_a_second_run_in_the_same_directory(tmp_path):
    script = loop_sim.converges_in_k_rounds(2)
    (tmp_path/'project').mkdir()
    ledger = CrashingLedger(tmp_path/'store', tmp_path/'project', 3)
    with pytest.raises(OSError): loop_sim.drive(script, tmp_path, ledger=ledger)
    with pytest.raises(LoopError) as caught: loop_sim.drive(script, tmp_path, ledger=ledger)
    assert caught.value.reason == 'loop_ledger_invalid'


@pytest.mark.parametrize('after', range(1, 11))
def test_an_interrupt_before_any_event_after_the_first_stops_as_interrupted_and_the_ledger_replays(tmp_path, after):
    script = loop_sim.interrupted_after_each_event(after)
    nominal = uninterrupted(script, tmp_path)
    result, ledger, _ = loop_sim.drive(script, tmp_path / 'run')
    recorded = ledger.events()
    assert recorded[:after] == nominal[:after] and len(recorded) == after + 1
    assert recorded[after][0] == 'stopped' and recorded[after][1]['value'] == 'interrupted'
    assert (result['stop'], ledger.replay(script.spec).stop) == ('interrupted', 'interrupted')
    decided = [payload['value'] for kind, payload in nominal[:after] if kind == 'decided']
    assert result['outcome'] == (decided[-1] if decided else None)


def test_an_interrupt_before_the_first_event_has_nothing_to_record_and_propagates(tmp_path):
    with pytest.raises(KeyboardInterrupt):
        loop_sim.drive(loop_sim.interrupted_after_each_event(0), tmp_path)
    assert not list((tmp_path/'store').rglob('0000.json'))


@pytest.mark.parametrize('at', [1, 2, 6])
def test_an_interrupt_in_the_middle_of_publishing_an_event_discards_it_and_records_the_interruption(tmp_path, at):
    script = loop_sim.converges_in_k_rounds(2)
    (tmp_path/'project').mkdir()
    ledger = CrashingLedger(tmp_path/'store', tmp_path/'project', at, KeyboardInterrupt)
    result, _, _ = loop_sim.drive(script, tmp_path, ledger=ledger)
    kinds = [kind for kind, _ in ledger.events()]
    assert (result['stop'], kinds[-1], kinds[:at]) == ('interrupted', 'stopped', [kind for kind, _ in uninterrupted(script, tmp_path)][:at]) and len(kinds) == at + 1
    assert ledger.replay(script.spec).stop == 'interrupted'


@pytest.mark.parametrize('damage', ['edit', 'delete', 'stray', 'foreign'])
def test_a_tampered_or_incomplete_ledger_is_refused_with_a_typed_error_when_read(tmp_path, damage):
    script = loop_sim.converges_in_k_rounds(2)
    _, ledger, _ = loop_sim.drive(script, tmp_path)
    events = ledger.case_dir/'events'
    if damage == 'edit': (events/'0002.json').write_text((events/'0002.json').read_text().replace('"index": 2', '"index": 7'))
    if damage == 'delete': (events/'0003.json').unlink()
    if damage == 'stray': (events/'0011.json').write_text('{}')
    if damage == 'foreign': (events/'notes.txt').write_text('hello')
    with pytest.raises(LoopError) as caught:
        ledger.events()
    assert caught.value.reason == 'loop_ledger_invalid'


def test_a_ledger_replays_only_under_the_declaration_it_was_recorded_with(tmp_path):
    _, ledger, _ = loop_sim.drive(loop_sim.converges_in_k_rounds(2), tmp_path)
    with pytest.raises(LoopError) as caught:
        ledger.replay(loop_sim.regresses_on_round_n(1).spec)
    assert caught.value.reason == 'loop_ledger_invalid'


def test_a_ledger_that_never_began_cannot_be_read_or_written(tmp_path):
    (tmp_path/'project').mkdir()
    ledger = LoopLedger(tmp_path/'store', tmp_path/'project')
    for action in (ledger.events, lambda: ledger.write('spec_frozen', {}, elapsed=0.0)):
        with pytest.raises(LoopError) as caught: action()
        assert caught.value.reason == 'loop_ledger_invalid'
    assert not (tmp_path/'store').exists()


def test_a_ledger_cannot_begin_twice_and_a_run_needs_a_fresh_one(tmp_path):
    script = loop_sim.baseline_already_accepted()
    _, ledger, _ = loop_sim.drive(script, tmp_path)
    for action in (lambda: ledger.begin(script.spec), lambda: loop_sim.drive(script, tmp_path, ledger=ledger)):
        with pytest.raises(LoopError) as caught: action()
        assert caught.value.reason == 'loop_ledger_invalid'


def test_a_store_that_holds_unrelated_files_is_refused_and_left_alone(tmp_path):
    (tmp_path/'project').mkdir()
    (tmp_path/'store').mkdir(mode=0o700)
    (tmp_path/'store'/'notes.txt').write_text('keep me')
    ledger = LoopLedger(tmp_path/'store', tmp_path/'project')
    with pytest.raises(LoopError) as caught: ledger.begin(loop_sim.baseline_already_accepted().spec)
    assert caught.value.reason == 'loop_ledger_invalid' and [path.name for path in (tmp_path/'store').iterdir()] == ['notes.txt']


@pytest.mark.parametrize('where', ['inside', 'same', 'parent', 'symlink', 'missing-project', 'dotdot'])
def test_a_ledger_must_live_outside_the_project_on_real_directories(tmp_path, where):
    project = project_with_harness(tmp_path)
    (tmp_path/'real').mkdir()
    (tmp_path/'link').symlink_to(tmp_path/'real')
    before = project_state(project)
    root = dict(inside=project/'ledger', same=project, parent=tmp_path, symlink=tmp_path/'link'/'store', dotdot=tmp_path/'real'/'..'/'store',
                **{'missing-project': tmp_path/'store'})[where]
    with pytest.raises(LoopError) as caught:
        LoopLedger(root, tmp_path/'absent' if where == 'missing-project' else project)
    assert (caught.value.reason, caught.value.field) == ('loop_ledger_invalid', 'root') and project_state(project) == before


def test_the_controller_imports_nothing_from_the_workflow_even_indirectly():
    code = ('import sys, agent_harness_lab.loop.controller, agent_harness_lab.loop.ledger\n'
            'loaded = sorted(name for name in sys.modules if name.startswith("agent_harness_lab.") and "workflow" in name)\n'
            'assert not loaded, loaded\n')
    done = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env={**os.environ, 'PYTHONPATH': str(ROOT)}, capture_output=True, text=True, timeout=120)
    assert (done.returncode, done.stderr) == (0, '')


def test_a_run_makes_no_subprocess_or_network_call_of_its_own(tmp_path, monkeypatch):
    refused = []
    monkeypatch.setattr(subprocess, 'Popen', lambda *args, **kwargs: refused.append('subprocess'))
    monkeypatch.setattr(socket.socket, 'connect', lambda *args, **kwargs: refused.append('network'))
    result, _, _ = loop_sim.drive(loop_sim.converges_in_k_rounds(2), tmp_path)
    assert (result['stop'], refused) == ('acceptance_score_reached', [])
