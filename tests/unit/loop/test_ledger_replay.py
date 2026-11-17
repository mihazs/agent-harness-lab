"""Reopening and replaying a round ledger for a resume: what verifies, what is refused, and what is never written (#815).

Every ledger here is built by hand from events a run writes, in a tmp_path store outside a tmp_path project. No port, clock,
model or network is involved, so a refusal is shown to leave the store exactly as it was.
"""
import hashlib
import os

import pytest

from agent_harness_lab.loop.errors import LoopError
from agent_harness_lab.loop.ledger import MAX_RESUMES, LoopLedger, event_cap
from tests.support import loop_sim


def spec_for(**declaration):
    return loop_sim.build('replay', [0.5], declaration=dict(max_rounds=3, plateau_rounds=2, **declaration)).spec


def begun(tmp_path, spec=None, events=()):
    """A ledger that holds the given (kind, payload) events, with its claim given up as a finished process's is."""
    (tmp_path/'project').mkdir(exist_ok=True)
    spec = spec or spec_for()
    ledger = LoopLedger(tmp_path/'store', tmp_path/'project').begin(spec)
    for index, (kind, payload) in enumerate(events):
        ledger.write(kind, {'spec_sha256': spec.digest, **payload} if kind == 'spec_frozen' else payload, elapsed=float(index))
    ledger.unclaim()
    return spec


def reopened(tmp_path, spec):
    return LoopLedger(tmp_path/'store', tmp_path/'project').reopen(spec)


def case_files(tmp_path):
    return {path.relative_to(tmp_path/'store').as_posix(): path.read_bytes() for path in sorted((tmp_path/'store').rglob('*')) if path.is_file()}


BASELINE = [('spec_frozen', {}), ('baseline_evaluated', dict(value='valid'))]


def test_reopen_returns_the_events_the_run_wrote_and_holds_the_claim(tmp_path):
    spec = begun(tmp_path, events=BASELINE)
    ledger = reopened(tmp_path, spec)
    assert [kind for kind, _ in ledger.events()] == ['spec_frozen', 'baseline_evaluated'] and ledger.count == 2 and ledger.last_elapsed == 1.0


def test_reopen_writes_nothing(tmp_path):
    spec = begun(tmp_path, events=BASELINE)
    before = case_files(tmp_path)
    reopened(tmp_path, spec).unclaim()
    assert case_files(tmp_path) == before


def test_a_stop_followed_by_a_resumed_event_replays_as_the_position_before_the_stop(tmp_path):
    spec = begun(tmp_path, events=[*BASELINE, ('stopped', dict(value='interrupted')), ('resumed', {})])
    run = reopened(tmp_path, spec).replay(spec)
    assert (run.state, run.stop) == ('baseline_evaluated', None)


def test_a_last_interrupted_stop_replays_as_the_position_it_interrupted_only_when_asked_to(tmp_path):
    spec = begun(tmp_path, events=[*BASELINE, ('stopped', dict(value='interrupted'))])
    ledger = reopened(tmp_path, spec)
    assert (ledger.replay(spec).stop, ledger.replay(spec, interrupted=True).stop) == ('interrupted', None)


@pytest.mark.parametrize('stop', ['no_actionable_mechanism', 'evidence_insufficient', 'budget_exhausted'])
def test_a_resumed_event_after_a_final_stop_is_refused_and_the_run_stays_stopped(tmp_path, stop):
    spec = begun(tmp_path, events=[*BASELINE, ('stopped', dict(value=stop)), ('resumed', {})])
    with pytest.raises(LoopError) as caught:
        reopened(tmp_path, spec)
    assert (caught.value.reason, caught.value.field) == ('loop_ledger_invalid', 'resumed')


def test_an_event_after_a_resumed_event_must_still_be_legal_for_the_machine(tmp_path):
    spec = begun(tmp_path, events=[*BASELINE, ('stopped', dict(value='interrupted')), ('resumed', {}), ('decided', dict(value='improved'))])
    with pytest.raises(LoopError) as caught:
        reopened(tmp_path, spec)
    assert caught.value.reason == 'loop_order_violation'


def test_a_changed_declaration_is_resume_spec_changed_and_nothing_is_written(tmp_path):
    begun(tmp_path, events=BASELINE)
    before = case_files(tmp_path)
    with pytest.raises(LoopError) as caught:
        reopened(tmp_path, spec_for(regression_margin=0.1))
    assert caught.value.reason == 'resume_spec_changed' and case_files(tmp_path) == before


def test_a_run_that_is_alive_holds_its_case_so_a_resume_is_busy(tmp_path):
    (tmp_path/'project').mkdir()
    spec = spec_for()
    alive = LoopLedger(tmp_path/'store', tmp_path/'project').begin(spec)
    alive.write('spec_frozen', {'spec_sha256': spec.digest}, elapsed=0.0)
    with pytest.raises(LoopError) as caught:
        reopened(tmp_path, spec)
    assert caught.value.reason == 'resume_busy'
    alive.unclaim()
    assert reopened(tmp_path, spec).count == 1


def test_a_second_resume_is_busy_while_the_first_holds_the_case(tmp_path):
    spec = begun(tmp_path, events=BASELINE)
    first = reopened(tmp_path, spec)
    with pytest.raises(LoopError) as caught:
        reopened(tmp_path, spec)
    assert caught.value.reason == 'resume_busy' and first.count == 2


@pytest.mark.parametrize('damage', ['edit-middle', 'edit-newest', 'delete-middle', 'stray-file', 'delete-head', 'old-head', 'malformed-head'])
def test_a_tampered_ledger_does_not_verify(tmp_path, damage):
    spec = begun(tmp_path, events=[*BASELINE, ('round_started', {}), ('candidate_frozen', {})])
    case = next((tmp_path/'store'/'cases').iterdir())
    events = case/'events'
    if damage == 'edit-middle': (events/'0001.json').write_text((events/'0001.json').read_text().replace('"valid"', '"vacuous"'))
    if damage == 'edit-newest': (events/'0003.json').write_text((events/'0003.json').read_text().replace('"recorded_at": "3.000000"', '"recorded_at": "9.000000"'))
    if damage == 'delete-middle': (events/'0002.json').unlink()
    if damage == 'stray-file': (events/'0004.json').write_text('{}')
    if damage == 'delete-head': (case/'head.json').unlink()
    if damage == 'malformed-head': (case/'head.json').write_text('{"events": [1], "head_sha256": "x"}')
    if damage == 'old-head': (case/'head.json').write_text('{"events": 1, "head_sha256": "' + hashlib.sha256(b'other').hexdigest() + '"}')
    with pytest.raises(LoopError) as caught:
        reopened(tmp_path, spec)
    assert caught.value.reason == 'loop_ledger_invalid'


def test_a_head_one_event_behind_is_what_a_crash_between_the_two_writes_leaves_and_it_verifies(tmp_path):
    spec = begun(tmp_path, events=[*BASELINE, ('round_started', {})])
    case = next((tmp_path/'store'/'cases').iterdir())
    events = sorted((case/'events').iterdir())
    previous = hashlib.sha256(events[1].read_bytes()).hexdigest()
    (case/'head.json').write_text('{"events": 2, "head_sha256": "' + previous + '"}')
    assert reopened(tmp_path, spec).count == 3


@pytest.mark.parametrize('cases', [0, 2])
def test_a_store_that_does_not_hold_exactly_one_case_is_not_resumable(tmp_path, cases):
    spec = begun(tmp_path, events=BASELINE)
    if cases == 0:
        for path in sorted((tmp_path/'store'/'cases').rglob('*'), reverse=True): path.rmdir() if path.is_dir() else path.unlink()
    else: os.mkdir(tmp_path/'store'/'cases'/('0'*64), 0o700)
    with pytest.raises(LoopError) as caught:
        reopened(tmp_path, spec)
    assert caught.value.reason == 'loop_ledger_invalid'


def test_a_ledger_that_was_never_begun_cannot_be_reopened(tmp_path):
    (tmp_path/'project').mkdir()
    with pytest.raises(LoopError) as caught:
        reopened(tmp_path, spec_for())
    assert caught.value.reason == 'loop_ledger_invalid' and not (tmp_path/'store').exists()


def test_a_reopened_ledger_allows_two_more_events_for_each_resume_a_run_may_make_and_no_more(tmp_path):
    spec = begun(tmp_path, events=BASELINE)
    ledger = reopened(tmp_path, spec)
    assert (ledger.max_events, event_cap(spec)) == (event_cap(spec) + 3*MAX_RESUMES, 6*spec.max_rounds + 9)


def test_the_kept_head_follows_every_event_the_ledger_writes(tmp_path):
    spec = begun(tmp_path, events=BASELINE)
    ledger = reopened(tmp_path, spec)
    ledger.write('stopped', dict(value='interrupted'), elapsed=2.0)
    kept = (ledger.case_dir/'head.json').read_text()
    assert f'"events": {ledger.count}' in kept and ledger.head in kept


def test_a_verified_store_names_its_event_count_and_head_without_a_declaration(tmp_path):
    spec = begun(tmp_path, events=BASELINE)
    ledger = reopened(tmp_path, spec)
    count, head = LoopLedger(tmp_path/'store', tmp_path/'project').verified()
    assert (count, head) == (ledger.count, ledger.head)
