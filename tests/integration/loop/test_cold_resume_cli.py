"""An interrupted synthetic loop can continue in a fresh CLI process."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from agent_harness_lab.loop import controller
from tests.support import loop_improve as li


def test_hardlinked_runtime_does_not_prevent_a_normal_loop(monkeypatch):
    with ExitStack() as stack:
        project = li.LoopProject(stack, monkeypatch, loop={'alpha': 0.999}, script={0: dict(harness=li.EVERYTHING)})
        os.link(project.fixture.agent, project.fixture.root / 'runtime-hardlink')
        code, receipt, _ = project.invoke('--accept-score', '0.1')
        assert code == 0 and receipt['loop']['stop_criterion'] == 'acceptance_score_reached'
        assert len(project.calls()) == 1


@pytest.mark.parametrize('hardlinked', [False, True])
def test_cold_resume_after_interruption_before_proposal(monkeypatch, hardlinked):
    with ExitStack() as stack:
        project = li.LoopProject(stack, monkeypatch, loop={'alpha': 0.999}, script={0: dict(harness=li.EVERYTHING)})
        if hardlinked: os.link(project.fixture.agent, project.fixture.root / 'runtime-hardlink')
        with monkeypatch.context() as interrupted:
            def stop(self):
                raise KeyboardInterrupt
            interrupted.setattr(controller._Loop, 'next_candidate', stop)
            code, receipt, _ = project.invoke('--accept-score', '0.1')
        assert code == 1 and receipt['loop']['stop_criterion'] == 'interrupted'
        assert project.calls() == []
        env = dict(os.environ, AHL_STATE_HOME=str(project.fixture.root / 'state'),
                   PYTHONPATH=str(Path(__file__).resolve().parents[3]))
        result = subprocess.run([sys.executable, '-m', 'agent_harness_lab.cli', 'improve', str(project.project),
                                 '--resume', Path(receipt['output']).name], env=env, capture_output=True, text=True, check=False)
        resumed = json.loads(result.stdout)
        assert result.returncode == 0 and resumed['loop']['stop_criterion'] == 'acceptance_score_reached'
        assert (resumed['scope'], resumed['scope_source'], resumed['layers_evaluated']) == ('project', 'default', ['project'])
        assert len(project.calls()) == 1


def test_cold_resume_before_baseline_dispatch_starts_the_baseline(monkeypatch):
    with ExitStack() as stack:
        project = li.LoopProject(stack, monkeypatch, loop={'alpha': 0.999}, script={0: dict(harness=li.EVERYTHING)})
        with monkeypatch.context() as interrupted:
            def stop(self):
                raise KeyboardInterrupt
            interrupted.setattr(controller._Loop, 'baseline', stop)
            code, receipt, _ = project.invoke('--accept-score', '0.1')
        assert code == 1 and receipt['loop']['stop_criterion'] == 'interrupted' and project.calls() == []
        env = dict(os.environ, AHL_STATE_HOME=str(project.fixture.root / 'state'),
                   PYTHONPATH=str(Path(__file__).resolve().parents[3]))
        result = subprocess.run([sys.executable, '-m', 'agent_harness_lab.cli', 'improve', str(project.project),
                                 '--resume', Path(receipt['output']).name], env=env, capture_output=True, text=True, check=False)
        resumed = json.loads(result.stdout)
        assert result.returncode == 0 and resumed['loop']['stop_criterion'] == 'acceptance_score_reached'
        assert len(project.calls()) == 1 and resumed['loop']['before']['trials'] > 0


def test_cold_resume_reuses_the_validated_runtime_hash(monkeypatch):
    from agent_harness_lab.loop import cold_resume
    with ExitStack() as stack:
        project = li.LoopProject(stack, monkeypatch, loop={'alpha': 0.999}, script={0: dict(harness=li.EVERYTHING)})
        with monkeypatch.context() as interrupted:
            interrupted.setattr(controller._Loop, 'next_candidate', lambda self: (_ for _ in ()).throw(KeyboardInterrupt()))
            _, first, _ = project.invoke('--accept-score', '0.1')
        calls, real = [], cold_resume.runtime_sha256
        def capture(path):
            calls.append(path)
            return real(path)
        with monkeypatch.context() as patched:
            patched.setattr(cold_resume, 'runtime_sha256', capture)
            code, resumed, _ = project.invoke('--resume', Path(first['output']).name)
        assert code == 0 and resumed['loop']['stop_criterion'] == 'acceptance_score_reached'
        assert len(calls) == 1


def test_cold_resume_refuses_runtime_changed_after_validation(monkeypatch):
    from agent_harness_lab.loop import cold_resume
    with ExitStack() as stack:
        project = li.LoopProject(stack, monkeypatch, loop={'alpha': 0.999}, script={0: dict(harness=li.EVERYTHING)})
        with monkeypatch.context() as interrupted:
            interrupted.setattr(controller._Loop, 'next_candidate', lambda self: (_ for _ in ()).throw(KeyboardInterrupt()))
            _, first, _ = project.invoke('--accept-score', '0.1')
        real = cold_resume.read
        def change_after_read(output, project_path):
            saved = real(output, project_path)
            agent = project.fixture.agent
            agent.write_text(agent.read_text() + '\n# synthetic runtime revision\n')
            return saved
        with monkeypatch.context() as patched:
            patched.setattr(cold_resume, 'read', change_after_read)
            code, _, _ = project.invoke('--resume', Path(first['output']).name)
        assert code != 0 and project.calls() == []


def test_frozen_loop_spec_can_be_reconstructed_without_changing_its_identity():
    from agent_harness_lab.loop.spec import LoopSpec, resolve
    original = resolve(limits={'seconds': 90, 'trials': 24}, cli={'accept_score': {'coding': 0.8}}, areas=('coding',))
    restored = LoopSpec.from_document(original.to_dict())
    assert restored == original and restored.to_dict() == original.to_dict()


def test_resume_context_is_frozen_and_bound_to_the_loop_ledger(monkeypatch):
    from agent_harness_lab.loop.ledger import LoopLedger
    from agent_harness_lab.private_journal import record
    with ExitStack() as stack:
        project = li.LoopProject(stack, monkeypatch, loop={'alpha': 0.999}, script={0: dict(harness=li.EVERYTHING)})
        with monkeypatch.context() as interrupted:
            def stop(self):
                raise KeyboardInterrupt
            interrupted.setattr(controller._Loop, 'next_candidate', stop)
            _, receipt, _ = project.invoke('--accept-score', '0.1')
        output = Path(receipt['output'])
        context, digest = record(output / 'loop-resume-context.json', 8 * 1024 * 1024)
        ledger = LoopLedger(project.fixture.root / 'state' / 'loops' / output.name, project.project)
        _, _, events = ledger.verified(include_events=True)
        assert events[0][1]['resume_context_sha256'] == digest
        assert context['spec'] == receipt['loop']['spec']
        assert context['frozen_inputs_sha256'] == receipt['loop']['frozen_inputs_sha256']
        assert all(set(payload['stage_spent']) == {'construction', 'trials'} for _, payload in events)


def test_recovery_refuses_a_changed_context_before_any_new_work(tmp_path, monkeypatch):
    from dataclasses import replace
    import pytest
    from agent_harness_lab.loop.ledger import LoopLedger
    from tests.support import loop_sim
    script = replace(loop_sim.interrupted_after_each_event(), interrupt_after=None)
    first = loop_sim.ScriptedPorts(script, loop_sim.Meter())
    first.journal_proposal_dispatch = True
    first.resume_context_sha256 = 'a' * 64
    first.recovery_stage_spent = lambda: {'construction': 0.0, 'trials': 0.0}
    first.recovery_wall_ns = lambda: 1
    first.recovery_evaluation = lambda: {'synthetic': True}
    with monkeypatch.context() as interrupted:
        def stop(self):
            raise KeyboardInterrupt
        interrupted.setattr(controller._Loop, 'next_candidate', stop)
        result, ledger, _ = loop_sim.drive(script, tmp_path, ports=first)
    assert result['stop'] == 'interrupted'
    before = ledger.events()
    fresh = loop_sim.ScriptedPorts(script, loop_sim.Meter(), evaluated=1)
    fresh.journal_proposal_dispatch = True
    fresh.resume_context_sha256 = 'b' * 64
    fresh.recovery_stage_spent = lambda: {'construction': 0.0, 'trials': 0.0}
    with pytest.raises(controller.LoopError) as caught:
        controller.resume(script.spec, fresh, fresh, LoopLedger(tmp_path / 'store', tmp_path / 'project'),
                          lambda: fresh.meter.seconds)
    assert caught.value.reason == 'resume_inputs_changed' and fresh.asked == [] and ledger.events() == before


def test_reconstructed_spec_rejects_a_rehashed_invalid_declaration():
    import hashlib
    import pytest
    from agent_harness_lab.loop.spec import LoopSpec, resolve
    original = resolve(limits={'seconds': 90, 'trials': 24}).to_dict()
    original['automatic_promotion'] = True
    identity = {key: value for key, value in original.items() if key not in {'digest', 'sources', 'acceptance_source', 'looks'}}
    original['digest'] = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    with pytest.raises(ValueError):
        LoopSpec.from_document(original)


def test_recovery_refuses_ledger_changed_after_context_was_read(tmp_path, monkeypatch):
    from dataclasses import replace
    import pytest
    from agent_harness_lab.loop.ledger import LoopLedger
    from tests.support import loop_sim
    script = replace(loop_sim.interrupted_after_each_event(), interrupt_after=None)
    first = loop_sim.ScriptedPorts(script, loop_sim.Meter())
    with monkeypatch.context() as interrupted:
        def stop(self):
            raise KeyboardInterrupt
        interrupted.setattr(controller._Loop, 'next_candidate', stop)
        result, ledger, _ = loop_sim.drive(script, tmp_path, ports=first)
    assert result['stop'] == 'interrupted'
    before = ledger.events()
    fresh = loop_sim.ScriptedPorts(script, loop_sim.Meter(), evaluated=1)
    with pytest.raises(controller.LoopError) as caught:
        controller.resume(script.spec, fresh, fresh, LoopLedger(tmp_path / 'store', tmp_path / 'project'),
                          lambda: fresh.meter.seconds, expected_head='0' * 64)
    assert caught.value.reason == 'resume_inputs_changed' and fresh.asked == [] and ledger.events() == before


def test_recovery_reads_frozen_inputs_without_mutating_them_and_rejects_changes(monkeypatch):
    import pytest
    from agent_harness_lab.loop import cold_resume
    with ExitStack() as stack:
        project = li.LoopProject(stack, monkeypatch, loop={'alpha': 0.999}, script={0: dict(harness=li.EVERYTHING)})
        with monkeypatch.context() as interrupted:
            def stop(self):
                raise KeyboardInterrupt
            interrupted.setattr(controller._Loop, 'next_candidate', stop)
            _, receipt, _ = project.invoke('--accept-score', '0.1')
        output = Path(receipt['output'])
        brief = output / 'analysis-brief.json'
        before = brief.read_bytes()
        saved = cold_resume.read(output, project.project)
        assert saved['context']['spec'] == receipt['loop']['spec'] and brief.read_bytes() == before
        brief.write_bytes(before + b' ')
        with pytest.raises(ValueError):
            cold_resume.read(output, project.project)
        assert project.calls() == []


def test_cold_resume_after_a_completed_round(monkeypatch):
    with ExitStack() as stack:
        first = 'solves=pairs,settings,responses'
        project = li.LoopProject(stack, monkeypatch, loop={'alpha': 0.999},
                                 script={0: dict(harness=first), 1: dict(harness=li.EVERYTHING)})
        original = controller._Loop.next_candidate
        def stop_after_one_round(self):
            if self.position.rounds:
                raise KeyboardInterrupt
            return original(self)
        with monkeypatch.context() as interrupted:
            interrupted.setattr(controller._Loop, 'next_candidate', stop_after_one_round)
            code, receipt, _ = project.invoke('--accept-score', '0.1', '--focus', 'synthetic')
        assert code == 1 and receipt['loop']['stop_criterion'] == 'interrupted' and len(project.calls()) == 1
        env = dict(os.environ, AHL_STATE_HOME=str(project.fixture.root / 'state'),
                   PYTHONPATH=str(Path(__file__).resolve().parents[3]))
        result = subprocess.run([sys.executable, '-m', 'agent_harness_lab.cli', 'improve', str(project.project),
                                 '--resume', Path(receipt['output']).name], env=env, capture_output=True, text=True, check=False)
        resumed = json.loads(result.stdout)
        assert result.returncode == 0 and resumed['loop']['stop_criterion'] == 'acceptance_score_reached'
        assert len(project.calls()) == 2


@pytest.mark.parametrize('interrupt_before_preservation', [False, True])
def test_settled_partial_baseline_is_preserved_before_fresh_evaluation(monkeypatch, interrupt_before_preservation):
    from agent_harness_lab.loop import improve
    with ExitStack() as stack:
        project = li.LoopProject(stack, monkeypatch, loop={'alpha': 0.999}, script={0: dict(harness=li.EVERYTHING)})
        def stop_baseline(self, snapshot, frozen_inputs):
            partial = self.inner.work / 'evaluation-0'
            partial.mkdir()
            (partial / 'partial-result').write_text('incomplete')
            raise KeyboardInterrupt
        with monkeypatch.context() as interrupted:
            interrupted.setattr(improve.Charged, 'evaluate', stop_baseline)
            _, first, _ = project.invoke('--accept-score', '0.1')
        assert first['loop']['stop_reason'] == 'unresolved_request' and project.calls() == []
        with monkeypatch.context() as settled:
            settled.setattr(improve.Charged, 'settle', lambda self, snapshot: 2, raising=False)
            if interrupt_before_preservation:
                settled.setattr(improve.Charged, 'preserve_abandoned', lambda self, *args: (_ for _ in ()).throw(KeyboardInterrupt()))
            code, result, _ = project.invoke('--resume', Path(first['output']).name)
        if interrupt_before_preservation:
            assert code == 1 and result['loop']['stop_criterion'] == 'interrupted'
            code, final, _ = project.invoke('--resume', Path(first['output']).name)
        else:
            final = result
        assert code == 0 and final['loop']['stop_criterion'] == 'acceptance_score_reached', final
        assert final['loop']['usage']['trials'] == 2 + final['loop']['before']['trials'] + final['loop']['rounds'][0]['trials']
        archive = 'abandoned-evaluation-0-2' if interrupt_before_preservation else 'abandoned-evaluation-0-1'
        assert (Path(first['output']) / archive / 'evaluation-0' / 'partial-result').read_text() == 'incomplete'


def test_settled_baseline_does_not_start_again_without_enough_trial_budget(monkeypatch):
    from agent_harness_lab.loop import improve
    with ExitStack() as stack:
        project = li.LoopProject(stack, monkeypatch, loop={'alpha': 0.999}, script={0: dict(harness=li.EVERYTHING)})
        def partial_baseline(self, *args):
            partial = self.inner.work / 'evaluation-0'
            partial.mkdir()
            (partial / 'partial-result').write_text('incomplete')
            raise KeyboardInterrupt
        with monkeypatch.context() as interrupted:
            interrupted.setattr(improve.Charged, 'evaluate', partial_baseline)
            _, first, _ = project.invoke('--accept-score', '0.1')
        spent = first['loop']['spec']['limits']['trials'] - 5  # the frozen bank needs six baseline trials
        with monkeypatch.context() as settled:
            settled.setattr(improve.Charged, 'settle', lambda self, snapshot: spent, raising=False)
            code, final, _ = project.invoke('--resume', Path(first['output']).name)
        assert code == 1 and final['loop']['stop_criterion'] == 'budget_exhausted'
        assert final['loop']['usage']['trials'] == spent and final['loop']['before'] is None
        partial = Path(first['output']) / 'evaluation-0' / 'partial-result'
        assert partial.read_text() == 'incomplete' and project.calls() == []
        code, repeated, _ = project.invoke('--resume', Path(first['output']).name)
        assert code == 1 and repeated == final and partial.read_text() == 'incomplete' and project.calls() == []


def test_cold_resume_does_not_repeat_an_unresolved_evaluation(monkeypatch):
    from agent_harness_lab.loop import improve
    with ExitStack() as stack:
        project = li.LoopProject(stack, monkeypatch, loop={'alpha': 0.999}, script={0: dict(harness=li.EVERYTHING)})
        original = improve.Charged.evaluate
        def stop_candidate(self, snapshot, frozen_inputs):
            if self.inner.runs:
                raise KeyboardInterrupt
            return original(self, snapshot, frozen_inputs)
        with monkeypatch.context() as interrupted:
            interrupted.setattr(improve.Charged, 'evaluate', stop_candidate)
            code, receipt, _ = project.invoke('--accept-score', '0.1')
        assert code == 1 and receipt['loop']['stop_criterion'] == 'interrupted' and len(project.calls()) == 1
        env = dict(os.environ, AHL_STATE_HOME=str(project.fixture.root / 'state'),
                   PYTHONPATH=str(Path(__file__).resolve().parents[3]))
        result = subprocess.run([sys.executable, '-m', 'agent_harness_lab.cli', 'improve', str(project.project),
                                 '--resume', Path(receipt['output']).name], env=env, capture_output=True, text=True, check=False)
        resumed = json.loads(result.stdout)
        assert result.returncode == 1 and resumed['loop']['stop_criterion'] == 'interrupted'
        assert resumed['loop']['stop_reason'] == 'unresolved_request' and len(project.calls()) == 1
        assert resumed['loop']['usage']['pending_usage'] == 'unknown'
        assert resumed['loop']['usage']['recorded_values'] == 'lower_bounds'
        assert 'built-in CLI cannot yet consume reconciliation' in resumed['loop']['remediation']


@pytest.mark.parametrize('interrupt_before_preservation', [False, True])
def test_settled_partial_evaluation_can_be_abandoned_and_resumed_again(monkeypatch, interrupt_before_preservation):
    from agent_harness_lab.loop import improve
    with ExitStack() as stack:
        project = li.LoopProject(stack, monkeypatch, loop={'alpha': 0.999}, script={0: dict(harness=li.EVERYTHING), 1: dict(harness=li.EVERYTHING)})
        real = improve.Charged.evaluate
        def stop_candidate(self, snapshot, frozen_inputs):
            if self.inner.runs:
                partial = self.inner.work / 'evaluation-1'
                partial.mkdir()
                (partial / 'partial-result').write_text('incomplete')
                raise KeyboardInterrupt
            return real(self, snapshot, frozen_inputs)
        with monkeypatch.context() as interrupted:
            interrupted.setattr(improve.Charged, 'evaluate', stop_candidate)
            _, first, _ = project.invoke('--accept-score', '0.1')
        assert first['loop']['stop_reason'] == 'unresolved_request' and len(project.calls()) == 1
        with monkeypatch.context() as settled:
            settled.setattr(improve.Charged, 'settle', lambda self, snapshot: 2, raising=False)
            if interrupt_before_preservation:
                settled.setattr(improve.Charged, 'preserve_abandoned', lambda self, round_number: (_ for _ in ()).throw(KeyboardInterrupt()))
            else:
                settled.setattr(controller._Loop, 'close', lambda self: (_ for _ in ()).throw(KeyboardInterrupt()))
            _, second, _ = project.invoke('--resume', Path(first['output']).name)
        assert second['loop']['stop_criterion'] == 'interrupted'
        assert second['loop']['usage']['trials'] == first['loop']['usage']['trials'] + 2
        assert second['loop']['rounds'][0]['score'] is None
        code, final, _ = project.invoke('--resume', Path(first['output']).name)
        assert code == 0 and final['loop']['stop_criterion'] == 'acceptance_score_reached'
        assert final['loop']['resumed']['abandoned_rounds'] == 1 and len(project.calls()) == 2
        assert final['loop']['usage']['trials'] == first['loop']['usage']['trials'] + 2 + final['loop']['rounds'][-1]['trials']
        assert final['loop']['rounds'][-1]['evaluation_id'] == final['loop']['after']['evaluation_id'] == 'evaluation-1'
        assert final['loop']['rounds'][-1]['paired'] is not None
        assert (Path(first['output']) / 'abandoned-evaluation-1' / 'evaluation-1' / 'partial-result').read_text() == 'incomplete'
        code, repeated, _ = project.invoke('--resume', Path(first['output']).name)
        assert code == 0 and repeated == final and len(project.calls()) == 2


def test_abandoned_preservation_refuses_a_symlink_without_touching_its_target(tmp_path):
    from types import SimpleNamespace
    from agent_harness_lab.loop.improve import Charged
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'data').write_text('untouched')
    work = tmp_path / 'work'
    work.mkdir(mode=0o700)
    (work / 'evaluation-1').symlink_to(outside, target_is_directory=True)
    evaluator = SimpleNamespace(work=work, runs=[{}], spec=SimpleNamespace(max_rounds=2), frozen_inputs='a' * 64)
    evaluator.investigate = lambda *args: None
    with pytest.raises(ValueError):
        Charged(evaluator, None).preserve_abandoned(1)
    assert (outside / 'data').read_text() == 'untouched' and not (work / 'abandoned-evaluation-1').exists()


@pytest.mark.parametrize('archive_kind', ['symlink', 'occupied'])
def test_abandoned_preservation_does_not_overwrite_an_existing_archive(tmp_path, archive_kind):
    from types import SimpleNamespace
    from agent_harness_lab.loop.improve import Charged
    work, outside = tmp_path / 'work', tmp_path / 'outside'
    work.mkdir(mode=0o700)
    outside.mkdir(mode=0o700)
    source = work / 'evaluation-1'
    source.mkdir()
    (source / 'partial').write_text('new')
    archive = work / 'abandoned-evaluation-1'
    if archive_kind == 'symlink':
        archive.symlink_to(outside, target_is_directory=True)
        target = outside
    else:
        archive.mkdir(mode=0o700)
        target = archive
    (target / 'evaluation-1').mkdir()
    (target / 'evaluation-1' / 'partial').write_text('old')
    evaluator = SimpleNamespace(work=work, runs=[{}], spec=SimpleNamespace(max_rounds=2), frozen_inputs='a' * 64)
    evaluator.investigate = lambda *args: None
    with pytest.raises((ValueError, OSError)):
        Charged(evaluator, None).preserve_abandoned(1)
    assert (source / 'partial').read_text() == 'new'
    assert (target / 'evaluation-1' / 'partial').read_text() == 'old'


def test_repeated_partial_candidates_are_preserved_and_settled_separately(monkeypatch):
    from agent_harness_lab.loop import improve
    with ExitStack() as stack:
        project = li.LoopProject(stack, monkeypatch, loop={'alpha': 0.999, 'max_rounds': 5, 'plateau_rounds': 5},
                                 script={0: dict(harness=li.EVERYTHING), 1: dict(harness=li.EVERYTHING),
                                     2: dict(harness=li.EVERYTHING),
                                 })
        real, stopped = improve.Charged.evaluate, []
        def stop_twice(self, snapshot, frozen_inputs):
            if self.inner.runs and len(stopped) < 2:
                partial = self.inner.work / 'evaluation-1'
                partial.mkdir()
                stopped.append(len(stopped) + 1)
                (partial / 'partial-result').write_text(str(stopped[-1]))
                raise KeyboardInterrupt
            return real(self, snapshot, frozen_inputs)
        with monkeypatch.context() as patched:
            patched.setattr(improve.Charged, 'evaluate', stop_twice)
            patched.setattr(improve.Charged, 'settle', lambda self, snapshot: 2, raising=False)
            _, first, _ = project.invoke('--accept-score', '0.01')
            _, second, _ = project.invoke('--resume', Path(first['output']).name)
            code, final, _ = project.invoke('--resume', Path(first['output']).name)
        assert second['loop']['stop_reason'] == 'unresolved_request'
        assert code == 0 and final['loop']['stop_criterion'] == 'acceptance_score_reached', (code, final['loop']['stop_criterion'], final['loop']['mechanisms'])
        assert 0.01 <= final['loop']['after']['score']['lower'] < 0.1
        assert final['loop']['resumed']['abandoned_rounds'] == 2 and len(project.calls()) == 3
        assert final['loop']['usage']['trials'] == first['loop']['usage']['trials'] + 4 + final['loop']['rounds'][-1]['trials']
        for round_number in (1, 2):
            archive = Path(first['output']) / f'abandoned-evaluation-{round_number}' / 'evaluation-1' / 'partial-result'
            assert archive.read_text() == str(round_number)


def test_cold_resume_uses_a_completed_evaluation_without_repeating_it(monkeypatch):
    with ExitStack() as stack:
        project = li.LoopProject(stack, monkeypatch, loop={'alpha': 0.999}, script={0: dict(harness=li.EVERYTHING)})
        with monkeypatch.context() as interrupted:
            def stop(self, *args):
                raise KeyboardInterrupt
            interrupted.setattr(controller._Loop, 'conclude', stop)
            code, receipt, _ = project.invoke('--accept-score', '0.1')
        assert code == 1 and receipt['loop']['stop_criterion'] == 'interrupted' and len(project.calls()) == 1
        env = dict(os.environ, AHL_STATE_HOME=str(project.fixture.root / 'state'),
                   PYTHONPATH=str(Path(__file__).resolve().parents[3]))
        result = subprocess.run([sys.executable, '-m', 'agent_harness_lab.cli', 'improve', str(project.project),
                                 '--resume', Path(receipt['output']).name], env=env, capture_output=True, text=True, check=False)
        resumed = json.loads(result.stdout)
        assert result.returncode == 0 and resumed['loop']['stop_criterion'] == 'acceptance_score_reached'
        assert len(project.calls()) == 1 and resumed['loop']['usage']['trials'] == receipt['loop']['usage']['trials']


def test_cold_resume_preserves_a_completed_regression_and_its_investigation(monkeypatch):
    with ExitStack() as stack:
        project = li.LoopProject(stack, monkeypatch, baseline=li.EVERYTHING, loop={'alpha': 0.999},
                                 script={0: dict(harness=li.NOTHING)})
        with monkeypatch.context() as interrupted:
            def stop(self, *args):
                raise KeyboardInterrupt
            interrupted.setattr(controller._Loop, 'conclude', stop)
            code, receipt, _ = project.invoke('--accept-score', '0.9')
        assert code == 1 and receipt['loop']['stop_criterion'] == 'interrupted' and len(project.calls()) == 1
        env = dict(os.environ, AHL_STATE_HOME=str(project.fixture.root / 'state'),
                   PYTHONPATH=str(Path(__file__).resolve().parents[3]))
        result = subprocess.run([sys.executable, '-m', 'agent_harness_lab.cli', 'improve', str(project.project),
                                 '--resume', Path(receipt['output']).name], env=env, capture_output=True, text=True, check=False)
        resumed = json.loads(result.stdout)
        assert result.returncode == 3 and resumed['loop']['stop_criterion'] == 'regression'
        assert resumed['loop']['rounds'][0]['disposition'] == 'rejected'
        assert resumed['loop']['best_round'] == 0 and 'recommended_candidate' not in resumed
        assert resumed['investigations'][0]['cause'] == 'candidate_caused'
        assert len(project.calls()) == 1 and resumed['loop']['usage']['trials'] == receipt['loop']['usage']['trials']


def test_cold_resume_finishes_a_recorded_final_decision_without_new_work(monkeypatch):
    with ExitStack() as stack:
        project = li.LoopProject(stack, monkeypatch, loop={'alpha': 0.999}, script={0: dict(harness=li.EVERYTHING)})
        original = controller._Loop.stopped
        called = False
        def interrupt_once(self, stop, outcome, reason=None):
            nonlocal called
            if not called:
                called = True
                raise KeyboardInterrupt
            return original(self, stop, outcome, reason)
        with monkeypatch.context() as interrupted:
            interrupted.setattr(controller._Loop, 'stopped', interrupt_once)
            code, receipt, _ = project.invoke('--accept-score', '0.1')
        assert code == 1 and receipt['loop']['stop_criterion'] == 'interrupted' and len(project.calls()) == 1
        env = dict(os.environ, AHL_STATE_HOME=str(project.fixture.root / 'state'),
                   PYTHONPATH=str(Path(__file__).resolve().parents[3]))
        result = subprocess.run([sys.executable, '-m', 'agent_harness_lab.cli', 'improve', str(project.project),
                                 '--resume', Path(receipt['output']).name], env=env, capture_output=True, text=True, check=False)
        resumed = json.loads(result.stdout)
        assert result.returncode == 0 and resumed['loop']['stop_criterion'] == 'acceptance_score_reached'
        assert len(project.calls()) == 1 and resumed['loop']['usage']['trials'] == receipt['loop']['usage']['trials']


@pytest.mark.parametrize('damage', ['configuration', 'project', 'brief', 'context', 'bank', 'manifest', 'scoring', 'evaluation', 'runtime', 'runtime_hardlink', 'receipt_corrupt', 'receipt_score'])
def test_cold_resume_refuses_changed_inputs_before_new_work(monkeypatch, damage):
    with ExitStack() as stack:
        project = li.LoopProject(stack, monkeypatch, loop={'alpha': 0.999}, script={0: dict(harness=li.EVERYTHING)})
        alias = project.fixture.root / 'runtime-hardlink'
        if damage == 'runtime_hardlink': os.link(project.fixture.agent, alias)
        with monkeypatch.context() as interrupted:
            def stop(self):
                raise KeyboardInterrupt
            interrupted.setattr(controller._Loop, 'next_candidate', stop)
            code, receipt, _ = project.invoke('--accept-score', '0.1')
        assert code == 1 and receipt['loop']['stop_criterion'] == 'interrupted' and project.calls() == []
        output = Path(receipt['output'])
        target = {'configuration': project.project / 'ahl.json', 'project': project.project / 'AGENTS.md',
                  'brief': output / 'analysis-brief.json', 'context': output / 'loop-resume-context.json',
                  'bank': output / 'preparation/evaluation-0/fixtures/localization/task.json',
                  'manifest': output / 'preparation/evaluation-0/manifest.json',
                  'scoring': output / 'preparation/evaluation-0/loop-scoring-manifest.json',
                  'evaluation': output / 'loop-evaluation-0.json', 'runtime': project.fixture.agent, 'runtime_hardlink': alias,
                  'receipt_corrupt': output / 'workflow.json', 'receipt_score': output / 'workflow.json'}[damage]
        if damage == 'scoring':
            target.unlink()
        elif damage == 'receipt_corrupt':
            target.write_text('{')
        elif damage == 'receipt_score':
            changed = json.loads(target.read_text())
            changed['loop']['before']['score']['point'] = 0.5
            target.write_text(json.dumps(changed))
        elif damage == 'configuration':
            changed = json.loads(target.read_text())
            changed['limits']['trials'] += 1
            target.write_text(json.dumps(changed))
        else:
            target.write_bytes(target.read_bytes() + b' ')
        env = dict(os.environ, AHL_STATE_HOME=str(project.fixture.root / 'state'),
                   PYTHONPATH=str(Path(__file__).resolve().parents[3]))
        result = subprocess.run([sys.executable, '-m', 'agent_harness_lab.cli', 'improve', str(project.project),
                                 '--resume', output.name], env=env, capture_output=True, text=True, check=False)
        assert result.returncode != 0 and project.calls() == []


def test_cold_resume_does_not_repeat_an_unresolved_proposal(monkeypatch):
    from agent_harness_lab.loop import improve
    with ExitStack() as stack:
        project = li.LoopProject(stack, monkeypatch, loop={'alpha': 0.999}, script={0: dict(harness=li.EVERYTHING)})
        def uncertain(self, best_snapshot, mechanism):
            project.calls_path.write_text(json.dumps({'attempt': 0, 'mechanism': mechanism}) + '\n')
            raise KeyboardInterrupt
        with monkeypatch.context() as interrupted:
            interrupted.setattr(improve.PreparationProposer, 'propose', uncertain)
            code, receipt, _ = project.invoke('--accept-score', '0.1')
        assert code == 1 and receipt['loop']['stop_criterion'] == 'interrupted' and len(project.calls()) == 1
        assert receipt['loop']['stop_reason'] == 'unresolved_request' and receipt['loop']['usage']['pending_usage'] == 'unknown'
        env = dict(os.environ, AHL_STATE_HOME=str(project.fixture.root / 'state'),
                   PYTHONPATH=str(Path(__file__).resolve().parents[3]))
        result = subprocess.run([sys.executable, '-m', 'agent_harness_lab.cli', 'improve', str(project.project),
                                 '--resume', Path(receipt['output']).name], env=env, capture_output=True, text=True, check=False)
        resumed = json.loads(result.stdout)
        assert result.returncode == 1 and resumed['loop']['stop_reason'] == 'unresolved_request'
        assert resumed['loop']['usage']['pending_usage'] == 'unknown' and len(project.calls()) == 1


def test_cold_resume_refuses_a_changed_frozen_candidate(monkeypatch):
    with ExitStack() as stack:
        project = li.LoopProject(stack, monkeypatch, loop={'alpha': 0.999}, script={0: dict(harness=li.EVERYTHING)})
        with monkeypatch.context() as interrupted:
            def stop(self, *args):
                raise KeyboardInterrupt
            interrupted.setattr(controller._Loop, 'conclude', stop)
            code, receipt, _ = project.invoke('--accept-score', '0.1')
        assert code == 1 and len(project.calls()) == 1
        output = Path(receipt['output'])
        candidate = output / 'candidate-0' / 'AGENTS.md'
        candidate.write_bytes(candidate.read_bytes() + b' ')
        env = dict(os.environ, AHL_STATE_HOME=str(project.fixture.root / 'state'),
                   PYTHONPATH=str(Path(__file__).resolve().parents[3]))
        result = subprocess.run([sys.executable, '-m', 'agent_harness_lab.cli', 'improve', str(project.project),
                                 '--resume', output.name], env=env, capture_output=True, text=True, check=False)
        assert result.returncode != 0 and len(project.calls()) == 1


def test_cold_resume_counts_time_since_the_last_durable_event(monkeypatch):
    with ExitStack() as stack:
        project = li.LoopProject(stack, monkeypatch, loop={'alpha': 0.999}, script={0: dict(harness=li.EVERYTHING)})
        with monkeypatch.context() as interrupted:
            def stop(self):
                raise KeyboardInterrupt
            interrupted.setattr(controller._Loop, 'next_candidate', stop)
            from agent_harness_lab.loop import improve
            interrupted.setattr(improve.time, 'time_ns', lambda: 1_000_000_000)
            code, receipt, _ = project.invoke('--accept-score', '0.1')
        assert code == 1 and project.calls() == []
        env = dict(os.environ, AHL_STATE_HOME=str(project.fixture.root / 'state'),
                   PYTHONPATH=str(Path(__file__).resolve().parents[3]))
        result = subprocess.run([sys.executable, '-m', 'agent_harness_lab.cli', 'improve', str(project.project),
                                 '--resume', Path(receipt['output']).name], env=env, capture_output=True, text=True, check=False)
        resumed = json.loads(result.stdout)
        assert resumed['loop']['stop_criterion'] == 'budget_exhausted' and project.calls() == []


def test_cold_resume_names_a_changed_loop_spec(monkeypatch):
    with ExitStack() as stack:
        project = li.LoopProject(stack, monkeypatch, loop={'alpha': 0.999}, script={0: dict(harness=li.EVERYTHING)})
        with monkeypatch.context() as interrupted:
            def stop(self):
                raise KeyboardInterrupt
            interrupted.setattr(controller._Loop, 'next_candidate', stop)
            _, receipt, _ = project.invoke('--accept-score', '0.1')
        config_path = project.project / 'ahl.json'
        changed = json.loads(config_path.read_text())
        changed['loop']['alpha'] = 0.95
        config_path.write_text(json.dumps(changed))
        env = dict(os.environ, AHL_STATE_HOME=str(project.fixture.root / 'state'),
                   PYTHONPATH=str(Path(__file__).resolve().parents[3]))
        result = subprocess.run([sys.executable, '-m', 'agent_harness_lab.cli', 'improve', str(project.project),
                                 '--resume', Path(receipt['output']).name], env=env, capture_output=True, text=True, check=False)
        assert result.returncode != 0 and 'resume_spec_changed' in result.stdout and project.calls() == []


def test_cold_resume_does_not_repeat_an_unresolved_baseline(monkeypatch):
    from agent_harness_lab.loop import improve
    with ExitStack() as stack:
        project = li.LoopProject(stack, monkeypatch, loop={'alpha': 0.999}, script={0: dict(harness=li.EVERYTHING)})
        with monkeypatch.context() as interrupted:
            def stop(self, snapshot, frozen_inputs):
                raise KeyboardInterrupt
            interrupted.setattr(improve.Charged, 'evaluate', stop)
            code, receipt, _ = project.invoke('--accept-score', '0.1')
        assert code == 1 and receipt['loop']['stop_criterion'] == 'interrupted' and project.calls() == []
        env = dict(os.environ, AHL_STATE_HOME=str(project.fixture.root / 'state'),
                   PYTHONPATH=str(Path(__file__).resolve().parents[3]))
        result = subprocess.run([sys.executable, '-m', 'agent_harness_lab.cli', 'improve', str(project.project),
                                 '--resume', Path(receipt['output']).name], env=env, capture_output=True, text=True, check=False)
        resumed = json.loads(result.stdout)
        assert result.returncode == 1 and resumed['loop']['stop_reason'] == 'unresolved_request'
        assert resumed['loop']['usage']['pending_usage'] == 'unknown' and project.calls() == []


@pytest.mark.parametrize('simulated_reboot', [False, True], ids=['same-boot', 'previous-boot'])
def test_cold_resume_after_process_death_never_repeats_an_unresolved_request(monkeypatch, simulated_reboot):
    import signal
    import time
    with ExitStack() as stack:
        project = li.LoopProject(stack, monkeypatch, loop={'alpha': 0.999}, script={0: dict(wait=True, harness=li.EVERYTHING)})
        agent, child_file = project.fixture.agent, project.fixture.root / 'child.pid'
        text = agent.read_text().replace(" if step.get('crash'): raise SystemExit(3)",
            " if step.get('wait'):\n  import os, time; p(" + repr(str(child_file)) + ").write_text(str(os.getpid())); time.sleep(30)\n if step.get('crash'): raise SystemExit(3)")
        agent.write_text(text)
        env = dict(os.environ, AHL_STATE_HOME=str(project.fixture.root / 'state'),
                   PYTHONPATH=str(Path(__file__).resolve().parents[3]))
        command = [sys.executable, '-m', 'agent_harness_lab.cli', 'improve', str(project.project), '--accept-score', '0.1']
        process = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        child = None
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and not child_file.exists() and process.poll() is None:
                time.sleep(0.05)
            assert child_file.exists() and len(project.calls()) == 1
            child = int(child_file.read_text())
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
            runs = project.fixture.root / 'state' / 'runs'
            run_ids = list(runs.glob('*/run-*'))
            assert len(run_ids) == 1
            resume = [sys.executable, '-m', 'agent_harness_lab.cli', 'improve', str(project.project), '--resume', run_ids[0].name]
            live = subprocess.run(resume, env=env, capture_output=True, text=True, check=False, timeout=10)
            assert json.loads(live.stdout)['reason'] == 'workspace_busy' and len(project.calls()) == 1
            os.killpg(child, signal.SIGKILL)
            child = None
            if simulated_reboot:
                # Both processes have been killed; an old boot identity models the post-reboot state.
                from agent_harness_lab import workflow
                lock = project.fixture.root / 'state' / 'locks' / (workflow.identity(project.project) + '.lock')
                parts = lock.read_text().split(':')
                assert parts[0] == 'v2' and len(parts) == 4
                parts[1] = '00000000-0000-0000-0000-000000000000' if parts[1] != '00000000-0000-0000-0000-000000000000' else '11111111-1111-1111-1111-111111111111'
                lock.write_text(':'.join(parts))
            stopped = subprocess.run(resume, env=env, capture_output=True, text=True, check=False, timeout=10)
            resumed = json.loads(stopped.stdout)
            assert stopped.returncode == 1
            if simulated_reboot:
                assert resumed.get('loop', {}).get('stop_reason') == 'unresolved_request', resumed
            else:
                assert resumed.get('reason') == 'workspace_busy', resumed
            assert len(project.calls()) == 1
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            if child is not None:
                try: os.killpg(child, signal.SIGKILL)
                except ProcessLookupError: pass


def test_legacy_stale_project_lock_is_preserved(tmp_path, monkeypatch):
    from agent_harness_lab import workflow
    project, state = tmp_path / 'project', tmp_path / 'state'
    project.mkdir()
    folder = state / 'locks'
    folder.mkdir(parents=True, mode=0o700)
    lock = folder / (workflow.identity(project) + '.lock')
    lock.write_text('999999:0123456789abcdef0123456789abcdef')
    lock.chmod(0o600)
    monkeypatch.setattr(workflow, 'state_root', lambda: state)
    with pytest.raises(ValueError):
        with workflow.project_lock(project):
            pytest.fail('legacy lock must stay blocked')
    assert lock.read_text() == '999999:0123456789abcdef0123456789abcdef'


@pytest.mark.parametrize('suffix', ['.lock', '.lock.guard'])
def test_project_lock_refuses_symlinks_without_touching_their_target(tmp_path, monkeypatch, suffix):
    from agent_harness_lab import workflow
    project, state = tmp_path / 'project', tmp_path / 'state'
    project.mkdir()
    folder = state / 'locks'
    folder.mkdir(parents=True, mode=0o700)
    outside = tmp_path / 'outside'
    outside.write_text('untouched')
    (folder / (workflow.identity(project) + suffix)).symlink_to(outside)
    monkeypatch.setattr(workflow, 'state_root', lambda: state)
    with pytest.raises((ValueError, OSError)):
        with workflow.project_lock(project):
            pytest.fail('symlink must be refused')
    assert outside.read_text() == 'untouched'


def test_project_lock_recovers_only_a_verified_previous_boot(tmp_path, monkeypatch):
    from agent_harness_lab import workflow
    boot_path = Path('/proc/sys/kernel/random/boot_id')
    if not boot_path.is_file():
        pytest.skip('boot identity unavailable')
    project, state = tmp_path / 'project', tmp_path / 'state'
    project.mkdir()
    folder = state / 'locks'
    folder.mkdir(parents=True, mode=0o700)
    lock = folder / (workflow.identity(project) + '.lock')
    current = boot_path.read_text().strip()
    previous = '00000000-0000-0000-0000-000000000000' if current != '00000000-0000-0000-0000-000000000000' else '11111111-1111-1111-1111-111111111111'
    lock.write_text(f'v2:{previous}:999999:0123456789abcdef0123456789abcdef')
    lock.chmod(0o600)
    monkeypatch.setattr(workflow, 'state_root', lambda: state)
    with workflow.project_lock(project):
        assert lock.read_text().startswith(f'v2:{current}:')
        with pytest.raises(ValueError):
            with workflow.project_lock(project):
                pytest.fail('live lock must block')
    assert not lock.exists()
    lock.write_text(f'v2:{current}:999999:0123456789abcdef0123456789abcdef')
    lock.chmod(0o600)
    with pytest.raises(ValueError):
        with workflow.project_lock(project):
            pytest.fail('same-boot stale lock may still have live children')
    assert lock.exists()


def test_later_run_treats_a_resumed_score_as_historical_and_evaluates_a_fresh_baseline(monkeypatch):
    with ExitStack() as stack:
        project = li.LoopProject(stack, monkeypatch, loop={'alpha': 0.999}, script={0: dict(harness=li.EVERYTHING)})
        with monkeypatch.context() as interrupted:
            def stop(self):
                raise KeyboardInterrupt
            interrupted.setattr(controller._Loop, 'next_candidate', stop)
            _, receipt, _ = project.invoke('--accept-score', '0.1')
        env = dict(os.environ, AHL_STATE_HOME=str(project.fixture.root / 'state'),
                   PYTHONPATH=str(Path(__file__).resolve().parents[3]))
        result = subprocess.run([sys.executable, '-m', 'agent_harness_lab.cli', 'improve', str(project.project),
                                 '--resume', Path(receipt['output']).name], env=env, capture_output=True, text=True, check=False)
        assert result.returncode == 0
        project.write({})
        _, later, _ = project.invoke('--accept-score', '0.1')
        carry = later['loop']['carry_over']
        assert carry['historical']['label'] == 'historical_observation'
        assert carry['fresh']['label'] == 'fresh_evaluation' and carry['fresh']['before']['point'] == 0.0
        assert later['loop']['before']['evaluation_id'] == 'evaluation-0'


def test_cold_resume_does_not_dispatch_a_frozen_candidate_after_time_expires(monkeypatch):
    with ExitStack() as stack:
        project = li.LoopProject(stack, monkeypatch, loop={'alpha': 0.999}, script={0: dict(harness=li.EVERYTHING)})
        original = controller._Loop.record
        def stop_after_freeze(self, event, *args, **kwargs):
            result = original(self, event, *args, **kwargs)
            if event == 'candidate_frozen': raise KeyboardInterrupt
            return result
        with monkeypatch.context() as interrupted:
            interrupted.setattr(controller._Loop, 'record', stop_after_freeze)
            from agent_harness_lab.loop import improve
            interrupted.setattr(improve.time, 'time_ns', lambda: 1_000_000_000)
            _, first, _ = project.invoke('--accept-score', '0.1')
        code, result, _ = project.invoke('--resume', Path(first['output']).name)
        assert code == 1 and result['loop']['stop_criterion'] == 'budget_exhausted'
        assert len(project.calls()) == 1 and not (Path(first['output']) / 'evaluation-1').exists()


def test_cold_resume_starts_a_frozen_candidate_before_evaluation_dispatch(monkeypatch):
    with ExitStack() as stack:
        project = li.LoopProject(stack, monkeypatch, loop={'alpha': 0.999}, script={0: dict(harness=li.EVERYTHING)})
        original = controller._Loop.record
        def stop_after_freeze(self, event, *args, **kwargs):
            result = original(self, event, *args, **kwargs)
            if event == 'candidate_frozen': raise KeyboardInterrupt
            return result
        with monkeypatch.context() as interrupted:
            interrupted.setattr(controller._Loop, 'record', stop_after_freeze)
            code, receipt, _ = project.invoke('--accept-score', '0.1')
        assert code == 1 and len(project.calls()) == 1
        env = dict(os.environ, AHL_STATE_HOME=str(project.fixture.root / 'state'),
                   PYTHONPATH=str(Path(__file__).resolve().parents[3]))
        result = subprocess.run([sys.executable, '-m', 'agent_harness_lab.cli', 'improve', str(project.project),
                                 '--resume', Path(receipt['output']).name], env=env, capture_output=True, text=True, check=False)
        resumed = json.loads(result.stdout)
        assert result.returncode == 0 and resumed['loop']['stop_criterion'] == 'acceptance_score_reached'
        assert len(project.calls()) == 1 and resumed['loop']['resumed']['abandoned_rounds'] == 0


def test_cold_resume_refuses_a_proposal_lost_before_its_candidate_was_recorded(monkeypatch):
    with ExitStack() as stack:
        project = li.LoopProject(stack, monkeypatch, loop={'alpha': 0.999}, script={0: dict(harness=li.EVERYTHING)})
        original = controller._Loop.record
        def stop_before_freeze(self, event, *args, **kwargs):
            if event == 'candidate_frozen':
                raise KeyboardInterrupt
            return original(self, event, *args, **kwargs)
        with monkeypatch.context() as interrupted:
            interrupted.setattr(controller._Loop, 'record', stop_before_freeze)
            code, receipt, _ = project.invoke('--accept-score', '0.1')
        assert code == 1 and receipt['loop']['stop_reason'] == 'unresolved_request' and len(project.calls()) == 1
        env = dict(os.environ, AHL_STATE_HOME=str(project.fixture.root / 'state'),
                   PYTHONPATH=str(Path(__file__).resolve().parents[3]))
        result = subprocess.run([sys.executable, '-m', 'agent_harness_lab.cli', 'improve', str(project.project),
                                 '--resume', Path(receipt['output']).name], env=env, capture_output=True, text=True, check=False)
        resumed = json.loads(result.stdout)
        assert result.returncode == 1 and resumed['loop']['stop_reason'] == 'unresolved_request'
        assert len(project.calls()) == 1


def test_repeated_resume_keeps_an_unresolved_proposal_blocked(monkeypatch):
    from agent_harness_lab.loop import improve
    with ExitStack() as stack:
        project = li.LoopProject(stack, monkeypatch, loop={'alpha': 0.999}, script={0: dict(harness=li.EVERYTHING)})
        with monkeypatch.context() as interrupted:
            def before_proposal(self):
                raise KeyboardInterrupt
            interrupted.setattr(controller._Loop, 'next_candidate', before_proposal)
            _, first, _ = project.invoke('--accept-score', '0.1')
        calls = []
        with monkeypatch.context() as interrupted:
            def uncertain(self, best_snapshot, mechanism):
                calls.append(mechanism)
                raise KeyboardInterrupt
            interrupted.setattr(improve.PreparationProposer, 'propose', uncertain)
            _, second, _ = project.invoke('--resume', Path(first['output']).name)
            _, third, _ = project.invoke('--resume', Path(first['output']).name)
        assert len(calls) == 1
        assert second['loop']['stop_reason'] == third['loop']['stop_reason'] == 'unresolved_request'
        assert second['loop']['usage']['pending_usage'] == third['loop']['usage']['pending_usage'] == 'unknown'


def test_after_a_simulated_reboot_a_pre_dispatch_crash_can_continue(monkeypatch):
    import signal
    import time
    from agent_harness_lab import workflow
    with ExitStack() as stack:
        project = li.LoopProject(stack, monkeypatch, loop={'alpha': 0.999}, script={0: dict(harness=li.EVERYTHING)})
        marker = project.fixture.root / 'before-proposal'
        program = ("import sys,time; from pathlib import Path; from agent_harness_lab.loop import controller; "
                   "from agent_harness_lab.cli import main; "
                   "exec(\"def pause(self):\\n Path(" + repr(str(marker)) + ").write_text('ready')\\n time.sleep(30)\"); "
                   "controller._Loop.next_candidate=pause; sys.exit(main(sys.argv[1:]))")
        env = dict(os.environ, AHL_STATE_HOME=str(project.fixture.root / 'state'),
                   PYTHONPATH=str(Path(__file__).resolve().parents[3]))
        command = [sys.executable, '-c', program, 'improve', str(project.project), '--accept-score', '0.1']
        process = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and not marker.exists() and process.poll() is None:
                time.sleep(0.05)
            assert marker.exists() and project.calls() == []
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        run_ids = list((project.fixture.root / 'state' / 'runs').glob('*/run-*'))
        assert len(run_ids) == 1
        lock = project.fixture.root / 'state' / 'locks' / (workflow.identity(project.project) + '.lock')
        parts = lock.read_text().split(':')
        assert parts[0] == 'v2' and len(parts) == 4
        parts[1] = '00000000-0000-0000-0000-000000000000' if parts[1] != '00000000-0000-0000-0000-000000000000' else '11111111-1111-1111-1111-111111111111'
        lock.write_text(':'.join(parts))
        result = subprocess.run([sys.executable, '-m', 'agent_harness_lab.cli', 'improve', str(project.project),
                                 '--resume', run_ids[0].name], env=env, capture_output=True, text=True, check=False, timeout=20)
        resumed = json.loads(result.stdout)
        assert result.returncode == 0 and resumed['loop']['stop_criterion'] == 'acceptance_score_reached'
        assert len(project.calls()) == 1
