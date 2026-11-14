"""The controlled evaluation author stops each pass at its construction time box with a typed timeout (#739)."""
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent_harness_lab.evaluation import construction
from agent_harness_lab.evaluation.manifest_contract import ContractError

WHOLE = 1000.0  # seconds left in the whole run when the author starts


class Clock:
    """Real monotonic time plus an offset the fake role advances, so no test sleeps."""

    def __init__(self):
        self.start = time.monotonic()
        self.offset = 0.0

    def monotonic(self):
        return self.start + self.offset


@pytest.fixture
def clock(monkeypatch):
    fake = Clock()
    monkeypatch.setattr(construction, 'time', SimpleNamespace(monotonic=fake.monotonic))
    return fake


def author(tmp_path, clock, box, role, **options):
    """Run construction.author with run_role replaced by `role(deadline, prompt)`; return what the role saw."""
    (tmp_path / 'inputs').mkdir(exist_ok=True)
    seen = []

    def fake_role(client, backend, workspace, inputs, name, instructions, prompt, deadline):
        seen.append(dict(deadline=deadline - clock.start, prompt=prompt))
        return role(len(seen))

    with patch.object(construction, 'run_role', side_effect=fake_role):
        try:
            construction.author(MagicMock(), MagicMock(), tmp_path / 'inputs', tmp_path / 'inputs', tmp_path / 'work',
                                tmp_path / 'published', clock.start + WHOLE, box=box, **options)
        except BaseException as error:  # the role ends every case below: by overrun, by timeout or by a stop marker
            return seen, error
    return seen, None


class Stop(Exception):
    """A marker ending the author right after the pass under test."""


def stops(_):
    raise Stop


def test_a_pass_without_a_box_runs_to_the_whole_run_deadline(tmp_path, clock):
    seen, error = author(tmp_path, clock, None, stops)
    assert isinstance(error, Stop) and seen[0]['deadline'] == pytest.approx(WHOLE)  # (start + WHOLE) - start is not exact for a large monotonic start
    assert 'Time box' not in seen[0]['prompt']


@pytest.mark.parametrize('box, expected', [
    pytest.param(30, 30, id='box-inside-the-whole-run'),
    pytest.param(0, 0, id='zero-box'),
    pytest.param(WHOLE, WHOLE, id='box-equal-to-the-whole-run'),
    pytest.param(WHOLE * 5, WHOLE, id='box-beyond-the-whole-run-is-capped'),
])
def test_a_box_sets_the_pass_deadline_without_passing_the_whole_run_deadline(tmp_path, clock, box, expected):
    seen, error = author(tmp_path, clock, box, stops)
    assert isinstance(error, Stop) and seen[0]['deadline'] == pytest.approx(expected)


def test_the_prompt_states_the_box_in_whole_seconds(tmp_path, clock):
    seen, _ = author(tmp_path, clock, 42.9, stops)
    assert 'this construction pass is stopped after 42 seconds' in seen[0]['prompt']


def test_a_pass_that_runs_into_its_box_ends_as_a_typed_construction_timeout(tmp_path, clock):
    def overruns(_):
        clock.offset += 31
        raise TimeoutError('inference deadline exhausted')

    _, error = author(tmp_path, clock, 30, overruns)
    assert type(error).__name__ == 'ConstructionTimeout' and isinstance(error, TimeoutError)  # still a budget stop for every caller
    assert str(error) == 'evaluation_construction_timeout'


@pytest.mark.parametrize('box, spent', [
    pytest.param(WHOLE * 2, WHOLE, id='box-beyond-the-whole-run'),
    pytest.param(WHOLE, WHOLE, id='box-equal-to-the-whole-run'),
])
def test_a_pass_that_ran_out_of_the_whole_run_is_not_labeled_a_construction_timeout(tmp_path, clock, box, spent):
    def exhausts(_):
        clock.offset += spent
        raise TimeoutError('inference deadline exhausted')

    _, error = author(tmp_path, clock, box, exhausts)
    assert type(error) is TimeoutError and str(error) == 'inference deadline exhausted'


def test_a_timeout_before_the_box_elapsed_is_not_labeled_a_construction_timeout(tmp_path, clock):
    def early(_):
        clock.offset += 5
        raise TimeoutError('inference request deadline exhausted')

    _, error = author(tmp_path, clock, 30, early)
    assert type(error) is TimeoutError and str(error) == 'inference request deadline exhausted'


def test_a_repair_pass_gets_its_own_box_counted_from_its_start(tmp_path, clock):
    def first_pass_then_repair(number):
        if number == 1:
            clock.offset += 20
            return MagicMock(), []
        raise Stop

    defect = ContractError('evaluation_manifest_unreadable', 'evaluation_construction')
    with patch.object(construction, '_drop_authored_digests'), patch.object(construction, '_check_shape', side_effect=[defect]):
        seen, error = author(tmp_path, clock, 30, first_pass_then_repair, repairs=[])
    assert isinstance(error, Stop) and len(seen) == 2
    assert seen[0]['deadline'] == pytest.approx(30) and seen[1]['deadline'] == pytest.approx(20 + 30)
    assert 'Fix only this defect' in seen[1]['prompt']
