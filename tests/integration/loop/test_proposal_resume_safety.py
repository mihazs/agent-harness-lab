"""An interrupted proposal with unknown dispatch must not be repeated."""
from dataclasses import replace

import pytest

from agent_harness_lab.loop import controller
from agent_harness_lab.loop.ledger import LoopLedger
from tests.support import loop_sim
from tests.support.loop_sim import Meter, ScriptedPorts


@pytest.mark.parametrize('proof', [None, False, 1])
def test_resume_does_not_repeat_an_unresolved_proposal(tmp_path, proof):
    script = replace(loop_sim.interrupted_after_each_event(), interrupt_after=None)
    class InterruptedProposer(ScriptedPorts):
        def propose(self, best_snapshot, mechanism):
            self.asked.append((best_snapshot, mechanism))
            raise KeyboardInterrupt
    first = InterruptedProposer(script, Meter())
    result, _, _ = loop_sim.drive(script, tmp_path, ports=first)
    assert result['stop'] == 'interrupted' and len(first.asked) == 1
    fresh = ScriptedPorts(script, Meter(), evaluated=1)
    if proof is not None:
        fresh.proposal_retry_safe = lambda events: proof
    resumed = controller.resume(script.spec, fresh, fresh, LoopLedger(tmp_path / 'store', tmp_path / 'project'),
                                lambda: fresh.meter.seconds)
    assert resumed['stop'] == 'interrupted' and resumed['stop_reason'] == 'unresolved_request'
    assert fresh.asked == []


def test_explicit_no_dispatch_proof_allows_resume_without_mutating_ledger_events(tmp_path, monkeypatch):
    script = replace(loop_sim.interrupted_after_each_event(), interrupt_after=None)
    first = ScriptedPorts(script, Meter())
    with monkeypatch.context() as interrupted:
        def stop_before_proposal(self):
            raise KeyboardInterrupt
        interrupted.setattr(controller._Loop, 'next_candidate', stop_before_proposal)
        result, _, _ = loop_sim.drive(script, tmp_path, ports=first)
    assert result['stop'] == 'interrupted' and first.asked == []
    fresh = ScriptedPorts(script, Meter(), evaluated=1)
    def proof(events):
        events.clear()
        return True
    fresh.proposal_retry_safe = proof
    resumed = controller.resume(script.spec, fresh, fresh, LoopLedger(tmp_path / 'store', tmp_path / 'project'),
                                lambda: fresh.meter.seconds)
    assert resumed['stop'] == 'acceptance_score_reached' and fresh.asked


def test_resume_does_not_repeat_proposal_lost_before_candidate_freeze(tmp_path):
    from tests.integration.loop.test_resume import SCRIPT, interrupted, ledger_for
    _, ledger, _ = interrupted(SCRIPT, tmp_path, 3)
    assert any(kind == 'round_started' for kind, _ in ledger.events())
    fresh = ScriptedPorts(SCRIPT, Meter(), evaluated=1)
    resumed = controller.resume(SCRIPT.spec, fresh, fresh, ledger_for(tmp_path), lambda: fresh.meter.seconds)
    assert resumed['stop'] == 'interrupted' and resumed['stop_reason'] == 'unresolved_request'
    assert fresh.asked == []


def test_failed_retry_proof_does_not_dispatch_or_write_a_resumption(tmp_path):
    script = replace(loop_sim.interrupted_after_each_event(), interrupt_after=None)
    class InterruptedProposer(ScriptedPorts):
        def propose(self, best_snapshot, mechanism):
            raise KeyboardInterrupt
    result, ledger, _ = loop_sim.drive(script, tmp_path, ports=InterruptedProposer(script, Meter()))
    before = ledger.events()
    assert result['stop'] == 'interrupted'
    fresh = ScriptedPorts(script, Meter(), evaluated=1)
    def fail(events):
        raise OSError('proof unavailable')
    fresh.proposal_retry_safe = fail
    with pytest.raises(OSError, match='proof unavailable'):
        controller.resume(script.spec, fresh, fresh, LoopLedger(tmp_path / 'store', tmp_path / 'project'),
                          lambda: fresh.meter.seconds)
    assert fresh.asked == [] and ledger.events() == before


def test_ambiguous_proposal_stays_blocked_until_retry_safety_is_verified(tmp_path, monkeypatch):
    script = replace(loop_sim.interrupted_after_each_event(), interrupt_after=None)
    first = ScriptedPorts(script, Meter())
    with monkeypatch.context() as interrupted:
        def stop_before_proposal(self):
            raise KeyboardInterrupt
        interrupted.setattr(controller._Loop, 'next_candidate', stop_before_proposal)
        result, _, _ = loop_sim.drive(script, tmp_path, ports=first)
    assert result['stop'] == 'interrupted' and first.asked == []
    unknown = ScriptedPorts(script, Meter(), evaluated=1)
    stopped = controller.resume(script.spec, unknown, unknown, LoopLedger(tmp_path / 'store', tmp_path / 'project'),
                                lambda: unknown.meter.seconds)
    assert stopped['stop_reason'] == 'unresolved_request' and unknown.asked == []
    verified = ScriptedPorts(script, Meter(), evaluated=1)
    verified.proposal_retry_safe = lambda events: True  # the synthetic proposer never sent an external request
    resumed = controller.resume(script.spec, verified, verified, LoopLedger(tmp_path / 'store', tmp_path / 'project'),
                                lambda: verified.meter.seconds)
    assert resumed['stop'] == 'acceptance_score_reached' and verified.asked


def test_second_unresolved_proposal_is_not_retried_after_a_nonfinal_decision(tmp_path):
    script = loop_sim.converges_in_k_rounds(2)
    class InterruptedSecond(ScriptedPorts):
        def propose(self, best_snapshot, mechanism):
            if self.asked:
                self.asked.append((best_snapshot, mechanism))
                raise KeyboardInterrupt
            return super().propose(best_snapshot, mechanism)
    first = InterruptedSecond(script, Meter())
    result, ledger, _ = loop_sim.drive(script, tmp_path, ports=first)
    assert result['stop'] == 'interrupted' and len(first.asked) == 2
    assert [kind for kind, _ in ledger.events()][-2] == 'decided'
    fresh = ScriptedPorts(script, Meter(), evaluated=2, proposed=1)
    resumed = controller.resume(script.spec, fresh, fresh, LoopLedger(tmp_path / 'store', tmp_path / 'project'),
                                lambda: fresh.meter.seconds)
    assert resumed['stop_reason'] == 'unresolved_request' and fresh.asked == []


def test_recorded_final_decision_can_finish_without_proposal_proof(tmp_path):
    from tests.integration.loop.test_resume import SCRIPT, interrupted, ledger_for
    _, ledger, _ = interrupted(SCRIPT, tmp_path, 10)
    events = ledger.events()
    assert events[-1][0] == 'stopped' and events[-2][0] == 'decided' and events[-2][1]['stop'] is not None
    fresh = ScriptedPorts(SCRIPT, Meter(), evaluated=3, proposed=2)
    resumed = controller.resume(SCRIPT.spec, fresh, fresh, ledger_for(tmp_path), lambda: fresh.meter.seconds)
    assert resumed['stop'] == events[-2][1]['stop'] and fresh.asked == [] and fresh.order == []


def test_journaled_run_resumes_when_interrupted_before_proposal_dispatch(tmp_path, monkeypatch):
    script = replace(loop_sim.interrupted_after_each_event(), interrupt_after=None)
    first = ScriptedPorts(script, Meter())
    first.journal_proposal_dispatch = True
    with monkeypatch.context() as interrupted:
        def stop_before_proposal(self):
            raise KeyboardInterrupt
        interrupted.setattr(controller._Loop, 'next_candidate', stop_before_proposal)
        result, _, _ = loop_sim.drive(script, tmp_path, ports=first)
    assert result['stop'] == 'interrupted' and first.asked == []
    fresh = ScriptedPorts(script, Meter(), evaluated=1)
    fresh.journal_proposal_dispatch = True
    resumed = controller.resume(script.spec, fresh, fresh, LoopLedger(tmp_path / 'store', tmp_path / 'project'),
                                lambda: fresh.meter.seconds)
    assert resumed['stop'] == 'acceptance_score_reached' and [mechanism for _, mechanism in fresh.asked] == ['m1', 'm2']


def test_journaled_run_does_not_retry_after_proposal_dispatch(tmp_path):
    script = replace(loop_sim.interrupted_after_each_event(), interrupt_after=None)
    class InterruptedProposer(ScriptedPorts):
        journal_proposal_dispatch = True
        def propose(self, best_snapshot, mechanism):
            self.asked.append((best_snapshot, mechanism))
            raise KeyboardInterrupt
    first = InterruptedProposer(script, Meter())
    result, ledger, _ = loop_sim.drive(script, tmp_path, ports=first)
    assert result['stop'] == 'interrupted' and len(first.asked) == 1
    assert 'proposal_started' in [kind for kind, _ in ledger.events()]
    fresh = ScriptedPorts(script, Meter(), evaluated=1)
    fresh.journal_proposal_dispatch = True
    resumed = controller.resume(script.spec, fresh, fresh, LoopLedger(tmp_path / 'store', tmp_path / 'project'),
                                lambda: fresh.meter.seconds)
    assert resumed['stop_reason'] == 'unresolved_request' and fresh.asked == []


def test_legacy_run_cannot_infer_no_dispatch_from_missing_marker(tmp_path, monkeypatch):
    script = replace(loop_sim.interrupted_after_each_event(), interrupt_after=None)
    first = ScriptedPorts(script, Meter())
    with monkeypatch.context() as interrupted:
        def stop_before_proposal(self):
            raise KeyboardInterrupt
        interrupted.setattr(controller._Loop, 'next_candidate', stop_before_proposal)
        result, _, _ = loop_sim.drive(script, tmp_path, ports=first)
    assert result['stop'] == 'interrupted' and first.asked == []
    fresh = ScriptedPorts(script, Meter(), evaluated=1)
    fresh.journal_proposal_dispatch = True
    resumed = controller.resume(script.spec, fresh, fresh, LoopLedger(tmp_path / 'store', tmp_path / 'project'),
                                lambda: fresh.meter.seconds)
    assert resumed['stop_reason'] == 'unresolved_request' and fresh.asked == []


def test_journaled_run_refuses_resume_with_a_nonjournaled_proposer(tmp_path, monkeypatch):
    script = replace(loop_sim.interrupted_after_each_event(), interrupt_after=None)
    first = ScriptedPorts(script, Meter())
    first.journal_proposal_dispatch = True
    with monkeypatch.context() as interrupted:
        def stop_before_proposal(self):
            raise KeyboardInterrupt
        interrupted.setattr(controller._Loop, 'next_candidate', stop_before_proposal)
        result, ledger, _ = loop_sim.drive(script, tmp_path, ports=first)
    assert result['stop'] == 'interrupted'
    before = ledger.events()
    fresh = ScriptedPorts(script, Meter(), evaluated=1)
    with pytest.raises(controller.LoopError) as caught:
        controller.resume(script.spec, fresh, fresh, LoopLedger(tmp_path / 'store', tmp_path / 'project'),
                          lambda: fresh.meter.seconds)
    assert caught.value.reason == 'resume_inputs_changed' and fresh.asked == [] and ledger.events() == before


def test_journaled_run_resumes_after_decision_before_next_dispatch(tmp_path, monkeypatch):
    script = loop_sim.converges_in_k_rounds(2)
    first = ScriptedPorts(script, Meter())
    first.journal_proposal_dispatch = True
    original = controller._Loop.next_candidate
    def interrupt_second(self):
        if self.position.rounds:
            raise KeyboardInterrupt
        return original(self)
    with monkeypatch.context() as interrupted:
        interrupted.setattr(controller._Loop, 'next_candidate', interrupt_second)
        result, ledger, _ = loop_sim.drive(script, tmp_path, ports=first)
    assert result['stop'] == 'interrupted' and len(first.asked) == 1
    assert ledger.events()[-2][0] == 'decided'
    fresh = ScriptedPorts(script, Meter(), evaluated=2, proposed=1)
    fresh.journal_proposal_dispatch = True
    resumed = controller.resume(script.spec, fresh, fresh, LoopLedger(tmp_path / 'store', tmp_path / 'project'),
                                lambda: fresh.meter.seconds)
    assert resumed['stop'] == 'acceptance_score_reached' and len(fresh.asked) == 1
