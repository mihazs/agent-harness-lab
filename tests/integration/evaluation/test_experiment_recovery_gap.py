"""Recovery contract probes: real controller calls, synthetic transport only."""
from copy import copy, deepcopy
import os
import json
from pathlib import Path
import time

import pytest

import agent_harness_lab
from agent_harness_lab.evaluation import responses
from agent_harness_lab.evaluation.comparison_custody import Comparison, bundle_binding, hashed
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.plan import ExperimentSpec, schedule
from tests.integration.sandbox import test_sandbox_inference as inference
from tests.integration.evaluation import test_repeated_workflow as repeated
from tests.integration.sandbox import test_sandbox_kernel as kernel


def _fail(message):
    """Raise AssertionError, as TestCase.fail did, from inside a synthetic transport."""
    raise AssertionError(message)


class TestRecoveryCheckpoint:
    @pytest.fixture(autouse=True)
    def _workspace(self, tmp_path):
        expected=os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
        if expected:assert Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve())
        self.workspace = tmp_path
        self.deadline = time.monotonic()+10
        self.receipts = []; self.calls = 0
        self.settings = dict(model='synthetic', max_requests=3, max_tool_calls=3,
            max_output_tokens=100, max_request_bytes=32768, max_response_bytes=16384)

    def client(self, transport):
        return responses.Client(self.settings, None, self.deadline, transport=transport,
            record=lambda value:self.receipts.append(deepcopy(value)))

    def run_role(self, client):
        return responses.run_role(client, None, self.workspace, {}, 'executor',
            'Synthetic role.', 'Synthetic task.', self.deadline, trial_id='synthetic-trial-17')

    def test_reserved_checkpoint_identifies_actual_trial_before_transport(self):
        def transport(*args):
            self.calls += 1
            reserved = self.receipts[-1]
            assert reserved['request_attempts'] == 1
            assert reserved['request_pending']
            assert 'synthetic-trial-17' in json.dumps(reserved), 'durable reservation omits the actual active trial identity'
            raise ConnectionError('synthetic ambiguous transport')
        with pytest.raises(ConnectionError): self.run_role(self.client(transport))

    def test_ambiguous_transport_keeps_reservation_and_cannot_retry(self):
        def transport(*args):
            self.calls += 1
            raise ConnectionError('synthetic ambiguous transport')
        client = self.client(transport)
        with pytest.raises(ConnectionError): self.run_role(client)
        with pytest.raises(responses.InferenceError): self.run_role(client)
        assert self.calls == 1
        assert client.requests == 1
        assert client.pending
        assert client.poisoned

    def test_realized_comparison_trial_is_bound_before_ambiguous_transport(self):
        from agent_harness_lab.cli import evaluator
        baseline=self.workspace/'baseline'; baseline.mkdir()
        candidate=self.workspace/'candidate'; candidate.mkdir()
        work=self.workspace/'work'; work.mkdir()
        spec=ExperimentSpec('synthetic',(('task','root','family'),),1,1,2,2,0.,0.,.05,
            (('family',1.),),evaluator().digest(baseline),evaluator().digest(candidate),'g','r')
        selected=schedule(spec)[0]
        def transport(*args):
            self.calls+=1
            bound=self.receipts[-1]['request_binding']['comparison']
            assert bound['trial_id'] == selected['trial_id']
            assert bound['schedule_sha256'] == hashed(schedule(spec))
            assert bound['scheduled_trial_sha256'] == hashed(selected)
            raise ConnectionError('synthetic ambiguous transport')
        client=self.client(transport)
        bundles={name:bundle_binding(path,self.deadline) for name,path in
            (('baseline',baseline),('candidate',candidate))}
        with Comparison(spec,{'task':digest(work)},client,self.deadline,bundles=bundles) as comparison:
            comparison.before(selected,work,baseline if selected['version']=='baseline' else candidate,client)
            with pytest.raises(ConnectionError):
                responses.run_role(client,None,work,{},'executor','Synthetic.','Task.',self.deadline,
                    trial_id=selected['trial_id'])
        assert self.calls == 1
        assert client.report()['request_binding']['comparison']['trial_id'] == selected['trial_id']

    def test_role_scope_tampering_blocks_transport_and_keeps_reservation(self):
        client=self.client(lambda *args:_fail('transport must not run'))
        original=client._respond
        def changed(*args):
            client.actions.current['trial_id']='forged'
            return original(*args)
        client._respond=changed
        with pytest.raises(ValueError):self.run_role(client)
        assert client.requests == 1
        assert client.poisoned

    def test_unscoped_legacy_requests_do_not_invent_trial_binding(self):
        client=self.client(lambda *args:(_ for _ in ()).throw(ConnectionError('synthetic')))
        with pytest.raises(ConnectionError):client.respond([],self.deadline)
        assert 'request_binding' not in self.receipts[-1]

    def test_checkpoint_interruption_blocks_transport_without_refunding_reservation(self):
        client=self.client(lambda *args:_fail('transport must not run'))
        def interrupted(value):
            self.receipts.append(deepcopy(value))
            raise OSError('synthetic checkpoint interruption')
        client.record=interrupted
        with pytest.raises(OSError):self.run_role(client)
        assert client.requests == 1
        assert client.pending
        assert client.poisoned
        assert self.receipts[-1]['request_binding']['request_ordinal'] == 1
        client.record=None
        with pytest.raises(responses.InferenceError):self.run_role(client)
        assert client.requests == 1

    def test_successive_role_scopes_preserve_cumulative_request_cap(self):
        def transport(*args):
            self.calls+=1
            return inference.reply([inference.message('message-'+str(self.calls))])
        client=self.client(transport)
        for ordinal in range(1,self.settings['max_requests']+1):
            self.run_role(client)
            assert client.report()['request_binding']['request_ordinal'] == ordinal
            assert client.requests == ordinal
        with pytest.raises(responses.InferenceError):self.run_role(client)
        assert self.calls == self.settings['max_requests']
        assert client.requests == self.settings['max_requests']

    def test_duplicate_completed_message_is_not_a_fresh_completion(self):
        def transport(*args):
            self.calls+=1
            return inference.reply([inference.message('same-message')])
        client=self.client(transport)
        self.run_role(client)
        with pytest.raises(responses.InferenceError):self.run_role(client)
        with pytest.raises(responses.InferenceError):self.run_role(client)
        assert self.calls == 2
        assert client.requests == 2
        assert client.poisoned

    def test_copied_client_and_caller_binding_cannot_mint_owned_scope(self):
        client=self.client(lambda *args:_fail('transport must not run'))
        client.request_binding={'trial_id':'forged','completion_authority':True}
        assert 'request_binding' not in client.report()
        with pytest.raises(ValueError):self.run_role(copy(client))
        assert client.requests == 0

    def test_private_reservation_and_schedule_cannot_be_relabelled_for_export(self):
        from agent_harness_lab.evaluation.benchmark import public_export_allowed
        public=dict(publication_allowed=True,provenance=dict(source_kind='synthetic',approved_publication=True,
            source_ref='synthetic:recovery',derived_from_private=False))
        for marker in (dict(profile='controller-request-reservation-v1'),
                dict(profile='controller-realized-schedule-v1'),dict(request_binding={}),dict(realized_schedule=dict(public))):
            for wrapper in (None,'receipt','analysis','context','binding'):
                record=dict(public,**marker) if wrapper is None else dict(public,**{wrapper:dict(public,**marker)})
                assert not public_export_allowed([record])

    @pytest.mark.skipif(not hasattr(os,'fork'),reason='native fork required')
    def test_inherited_client_cannot_reserve_in_child(self):
        client=self.client(lambda *args:_fail('foreign transport'))
        child=os.fork()
        if child==0:
            try:client.respond([],self.deadline)
            except ValueError:os._exit(0)
            except BaseException:os._exit(1)
            os._exit(1)
        until=time.monotonic()+2;found=0
        while time.monotonic()<until:
            found,status=os.waitpid(child,os.WNOHANG)
            if found:break
            time.sleep(.01)
        if not found:
            import signal
            os.kill(child,signal.SIGKILL);os.waitpid(child,0)
            pytest.fail('client fork check timed out')
        assert os.waitstatus_to_exitcode(status) == 0
        assert client.requests == 0


@pytest.mark.skipif(not kernel.ENABLED,reason='explicit delegated kernel profile required')
class TestRecoveryHistory(repeated.PublicFixture,kernel.PytestKernelFixture):
    def setUp(self):
        super().setUp();self.configure()

    def test_public_controller_persists_realized_schedule_before_executor_request(self):
        from agent_harness_lab.evaluation.oracle_history import HistoryStore
        runtime=self.root/'runtime';pin=kernel.pinned_runtime(runtime)['sha256']
        original=self.transport;observed=[]
        def transport(*args):
            receipt=json.loads((self.root/'output/inference.json').read_bytes())
            binding=receipt.get('request_binding',{}).get('comparison')
            if binding is not None:
                store=HistoryStore(self.root/'output/private-history')
                records=list(store.root.glob('*/realized-schedule.json'))
                if not records:records=list(store.root.rglob('realized-schedule.json'))
                assert len(records) == 1
                assert binding['journal']['run_id'] == records[0].parent.name
                journal=store.opened(binding['journal']['run_id'])
                assert binding['journal']['source_manifest_sha256'] == journal[2]['source_manifest_sha256']
                rows=json.loads(records[0].read_bytes())['trials']
                assert hashed(rows) == binding['schedule_sha256']
                assert binding['scheduled_trial_sha256'] in [hashed(row) for row in rows]
                observed.append(binding['trial_id'])
            return original(*args)
        self.transport=transport
        code,result=self.invoke(runtime,pin,self.root/'output')
        assert code == 3, result
        assert observed
        assert len(set(observed)) == 12
        assert not result['automatic_promotion']
        store=HistoryStore(self.root/'output/private-history');run=result['controller_run_id']
        historical=store.historical_plan(run)
        realized=historical['realized_schedule']
        assert realized['status'] == 'observed'
        assert realized['trials'] == schedule(ExperimentSpec.parse(historical['plan']))
        assert realized['schedule_sha256'] == hashed(realized['trials'])
        assert not realized['publication_allowed']
        assert not historical['publication_allowed']
        case=store.checked(run)[0]
        target=case/'realized-schedule.json';original=target.read_bytes()
        try:
            changed=json.loads(original);changed['trials'][0]['trial_id']='forged'
            target.write_text(json.dumps(changed))
            with pytest.raises(ValueError):store.checked(run)
            with pytest.raises(ValueError):store.historical_plan(run)
        finally:target.write_bytes(original)
        assert len(set(observed)) == 12
        store.checked(run)

