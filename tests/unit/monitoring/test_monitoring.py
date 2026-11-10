import argparse
import json
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from agent_harness_lab.monitoring import state
from agent_harness_lab.workflow import watch
from agent_harness_lab.cli import main


class WatchProject:
    """Watch arguments for a synthetic project whose state root and evidence collection are patched."""

    def __init__(self, root):
        self.root = root
        self.args = argparse.Namespace(project=self.root, events=None, format='normalized', output=None,
            agent='codex', codex='synthetic-missing', claude='synthetic-missing', timeout=100,
            interval=1, once=True, focus=None, retry_delay=10, max_attempts=3, watch_budget=100)

    def patched(self):
        stack = ExitStack()
        for target, kw in (
            ('agent_harness_lab.workflow.state_root', {'return_value': self.root / 'state'}),
            ('agent_harness_lab.workflow.collect', {'return_value': {'fingerprint': 'evidence'}}),
            ('builtins.print', {}),
        ):
            stack.enter_context(patch(target, **kw))
        return stack

    def checkpoint(self):
        return next((self.root / 'state/watch').glob('*.json'))


@pytest.fixture
def mon(tmp_path):
    project = WatchProject(tmp_path)
    with project.patched():
        yield project


def test_transient_backoff_completion_and_restart(mon):
    with patch('agent_harness_lab.workflow.time.time', return_value=100), patch(
            'agent_harness_lab.workflow.random.random', return_value=.5), patch(
            'agent_harness_lab.workflow._run_improvement', return_value=dict(
                status='execution_error', failure_class='transient_infrastructure')) as run:
        assert watch(mon.args) == 1
        assert watch(mon.args) == 1
        assert run.call_count == 1
    checkpoint = state.load(mon.checkpoint())
    assert checkpoint['retry_after'] == 110
    with patch('agent_harness_lab.workflow.time.time', return_value=111), patch(
            'agent_harness_lab.workflow._run_improvement', return_value={'status': 'no_change'}) as run:
        assert watch(mon.args) == 0
        assert watch(mon.args) == 0
        assert run.call_count == 1
    assert state.load(mon.checkpoint())['attempt_count'] == 2


def test_auth_and_interrupted_attempts_require_intervention(mon):
    with patch('agent_harness_lab.workflow._run_improvement', return_value=dict(
            status='execution_error', failure_class='authentication')) as run:
        watch(mon.args); watch(mon.args)
        assert run.call_count == 1
        mon.args.codex = 'changed-config'
        watch(mon.args)
        assert run.call_count == 2
    checkpoint = state.load(mon.checkpoint())
    checkpoint['disposition'] = 'in_progress'
    state.atomic_json(mon.checkpoint(), checkpoint)
    with patch('agent_harness_lab.workflow._run_improvement') as run:
        assert watch(mon.args) == 1
        run.assert_not_called()


def test_attempt_and_elapsed_budget_caps(mon):
    record = state.new('f', 'c')
    record.update(disposition='execution_error', failure_class='transient_infrastructure')
    for attempt in range(1, 4):
        record['attempt_count'] = attempt
        state.finish(record, {'status': 'execution_error', 'failure_class': 'transient_infrastructure'},
                     0, 1, 10, .5)
    assert not state.eligible(record, 'f', 'c', 10000, 3, 100)
    record['attempt_count'] = 1; record['spent_seconds'] = 100
    assert not state.eligible(record, 'f', 'c', 10000, 3, 100)
    record['spent_seconds'] = 0; record['attempt_count'] = 100
    state.finish(record, {'status': 'execution_error', 'failure_class': 'transient_infrastructure'}, 0, 1, 3600, 1)
    assert record['retry_after'] <= 3600


def test_corrupt_checkpoint_is_preserved_and_legacy_does_not_retry(mon):
    path = mon.root / 'checkpoint.json'
    path.write_text('{broken')
    with pytest.raises(ValueError): state.load(path)
    assert path.read_text() == '{broken'
    path.write_text(json.dumps({'fingerprint': 'f'}))
    legacy = state.load(path)
    assert not state.eligible(legacy, 'f', 'changed', 0, 3, 100)
    assert state.eligible(legacy, 'new', 'changed', 0, 3, 100)


def test_exit_contract_unknown_and_budget_exhausted(mon):
    assert state.exit_code('future_unknown') == 1
    assert state.exit_code('blocked') == 3
    assert state.exit_code('no_change') == 0
    with patch('agent_harness_lab.workflow.improve', return_value={'status': 'budget_exhausted'}):
        assert main(['improve', str(mon.root)]) == 1
    with patch('agent_harness_lab.workflow._run_improvement', return_value={'status': 'budget_exhausted'}):
        assert watch(mon.args) == 1


def test_watch_lock_covers_scheduling_and_checkpoint(mon):
    from agent_harness_lab.workflow import project_lock
    with project_lock(mon.root), patch('agent_harness_lab.workflow._run_improvement') as run:
        with pytest.raises(ValueError): watch(mon.args)
        run.assert_not_called()


def test_atomic_state_permissions(mon):
    path = mon.root / 'new/checkpoint.json'
    state.atomic_json(path, state.new('f', 'c'))
    assert path.stat().st_mode & 0o777 == 0o600
    assert state.load(path)['checkpoint_version'] == 2


def test_completed_disposition_cannot_retry_and_zero_budget_is_invalid(mon):
    record = state.new('f', 'c', disposition='no_change')
    record['failure_class'] = 'transient_infrastructure'
    assert not state.eligible(record, 'f', 'c', 100, 3, 100)
    mon.args.watch_budget = 0
    with pytest.raises(ValueError): watch(mon.args)


def test_improve_runtime_exceptions_keep_shared_nonzero_status_contract(mon):
    runtime=mon.root/'synthetic-runtime'
    runtime.write_bytes(b''); runtime.chmod(0o700)
    for error in (TimeoutError('synthetic budget'),ConnectionError('synthetic outage')):
        with patch.dict('os.environ',{'PATH':''}),patch('agent_harness_lab.workflow._improve',side_effect=error) as run:
            assert main(['improve',str(mon.root),'--agent','codex','--codex',str(runtime)]) == 1
            run.assert_called_once()


def test_unavailable_runtime_blocks_before_runtime_execution(mon):
    missing=mon.root/'missing-runtime'
    with patch.dict('os.environ',{'PATH':''}),patch('agent_harness_lab.workflow._improve') as run:
        assert main(['improve',str(mon.root),'--agent','codex','--codex',str(missing)]) == 3
        run.assert_not_called()
    result=json.loads(next((mon.root/'state/runs').glob('*/run-*/workflow.json')).read_bytes())
    assert result['reason'] == 'agent_runtime_unavailable'


def test_corrupt_configuration_or_clock_cannot_reenable_paid_work(mon):
    path=mon.root/'checkpoint.json'
    for key,value in (('configuration',{}),('last_attempt',float('nan')),('checkpoint_version',2.0),('run_id',[])):
        record=state.new('f','c'); record[key]=value
        path.write_text(json.dumps(record))
        with pytest.raises(ValueError): state.load(path)
