"""Recovery contract probes: real controller calls, synthetic transport only."""
from copy import copy, deepcopy
import os
import json
from pathlib import Path
import tempfile
import time
import unittest

import agent_harness_lab
from agent_harness_lab.evaluation import responses
from agent_harness_lab.evaluation.comparison_custody import Comparison, bundle_binding, hashed
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.plan import ExperimentSpec, schedule
import test_sandbox_inference as inference
import test_repeated_workflow as repeated
import test_sandbox_kernel as kernel


class RecoveryCheckpointTests(unittest.TestCase):
    def setUp(self):
        expected=os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
        if expected:self.assertTrue(Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve()))
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.workspace = Path(folder.name)
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
            self.assertEqual(reserved['request_attempts'], 1)
            self.assertTrue(reserved['request_pending'])
            self.assertIn('synthetic-trial-17', json.dumps(reserved),
                'durable reservation omits the actual active trial identity')
            raise ConnectionError('synthetic ambiguous transport')
        with self.assertRaises(ConnectionError): self.run_role(self.client(transport))

    def test_ambiguous_transport_keeps_reservation_and_cannot_retry(self):
        def transport(*args):
            self.calls += 1
            raise ConnectionError('synthetic ambiguous transport')
        client = self.client(transport)
        with self.assertRaises(ConnectionError): self.run_role(client)
        with self.assertRaises(responses.InferenceError): self.run_role(client)
        self.assertEqual(self.calls, 1)
        self.assertEqual(client.requests, 1)
        self.assertTrue(client.pending)
        self.assertTrue(client.poisoned)

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
            self.assertEqual(bound['trial_id'],selected['trial_id'])
            self.assertEqual(bound['schedule_sha256'],hashed(schedule(spec)))
            self.assertEqual(bound['scheduled_trial_sha256'],hashed(selected))
            raise ConnectionError('synthetic ambiguous transport')
        client=self.client(transport)
        bundles={name:bundle_binding(path,self.deadline) for name,path in
            (('baseline',baseline),('candidate',candidate))}
        with Comparison(spec,{'task':digest(work)},client,self.deadline,bundles=bundles) as comparison:
            comparison.before(selected,work,baseline if selected['version']=='baseline' else candidate,client)
            with self.assertRaises(ConnectionError):
                responses.run_role(client,None,work,{},'executor','Synthetic.','Task.',self.deadline,
                    trial_id=selected['trial_id'])
        self.assertEqual(self.calls,1)
        self.assertEqual(client.report()['request_binding']['comparison']['trial_id'],selected['trial_id'])

    def test_role_scope_tampering_blocks_transport_and_keeps_reservation(self):
        client=self.client(lambda *args:self.fail('transport must not run'))
        original=client._respond
        def changed(*args):
            client.actions.current['trial_id']='forged'
            return original(*args)
        client._respond=changed
        with self.assertRaises(ValueError):self.run_role(client)
        self.assertEqual(client.requests,1)
        self.assertTrue(client.poisoned)

    def test_unscoped_legacy_requests_do_not_invent_trial_binding(self):
        client=self.client(lambda *args:(_ for _ in ()).throw(ConnectionError('synthetic')))
        with self.assertRaises(ConnectionError):client.respond([],self.deadline)
        self.assertNotIn('request_binding',self.receipts[-1])

    def test_checkpoint_interruption_blocks_transport_without_refunding_reservation(self):
        client=self.client(lambda *args:self.fail('transport must not run'))
        def interrupted(value):
            self.receipts.append(deepcopy(value))
            raise OSError('synthetic checkpoint interruption')
        client.record=interrupted
        with self.assertRaises(OSError):self.run_role(client)
        self.assertEqual(client.requests,1)
        self.assertTrue(client.pending)
        self.assertTrue(client.poisoned)
        self.assertEqual(self.receipts[-1]['request_binding']['request_ordinal'],1)
        client.record=None
        with self.assertRaises(responses.InferenceError):self.run_role(client)
        self.assertEqual(client.requests,1)

    def test_successive_role_scopes_preserve_cumulative_request_cap(self):
        def transport(*args):
            self.calls+=1
            return inference.reply([inference.message('message-'+str(self.calls))])
        client=self.client(transport)
        for ordinal in range(1,self.settings['max_requests']+1):
            self.run_role(client)
            self.assertEqual(client.report()['request_binding']['request_ordinal'],ordinal)
            self.assertEqual(client.requests,ordinal)
        with self.assertRaises(responses.InferenceError):self.run_role(client)
        self.assertEqual(self.calls,self.settings['max_requests'])
        self.assertEqual(client.requests,self.settings['max_requests'])

    def test_duplicate_completed_message_is_not_a_fresh_completion(self):
        def transport(*args):
            self.calls+=1
            return inference.reply([inference.message('same-message')])
        client=self.client(transport)
        self.run_role(client)
        with self.assertRaises(responses.InferenceError):self.run_role(client)
        with self.assertRaises(responses.InferenceError):self.run_role(client)
        self.assertEqual(self.calls,2)
        self.assertEqual(client.requests,2)
        self.assertTrue(client.poisoned)

    def test_copied_client_and_caller_binding_cannot_mint_owned_scope(self):
        client=self.client(lambda *args:self.fail('transport must not run'))
        client.request_binding={'trial_id':'forged','completion_authority':True}
        self.assertNotIn('request_binding',client.report())
        with self.assertRaises(ValueError):self.run_role(copy(client))
        self.assertEqual(client.requests,0)

    def test_private_reservation_and_schedule_cannot_be_relabelled_for_export(self):
        from agent_harness_lab.evaluation.benchmark import public_export_allowed
        public=dict(publication_allowed=True,provenance=dict(source_kind='synthetic',approved_publication=True,
            source_ref='synthetic:recovery',derived_from_private=False))
        for marker in (dict(profile='controller-request-reservation-v1'),
                dict(profile='controller-realized-schedule-v1'),dict(request_binding={})):
            for wrapper in (None,'receipt','analysis','context','binding'):
                record=dict(public,**marker) if wrapper is None else dict(public,**{wrapper:dict(public,**marker)})
                self.assertFalse(public_export_allowed([record]))

    @unittest.skipUnless(hasattr(os,'fork'),'native fork required')
    def test_inherited_client_cannot_reserve_in_child(self):
        client=self.client(lambda *args:self.fail('foreign transport'))
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
            self.fail('client fork check timed out')
        self.assertEqual(os.waitstatus_to_exitcode(status),0)
        self.assertEqual(client.requests,0)


@unittest.skipUnless(kernel.ENABLED,'explicit delegated kernel profile required')
class RecoveryHistoryTests(repeated.PublicFixture,kernel.KernelFixture):
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
                self.assertEqual(len(records),1)
                self.assertEqual(binding['journal']['run_id'],records[0].parent.name)
                journal=store.opened(binding['journal']['run_id'])
                self.assertEqual(binding['journal']['source_manifest_sha256'],journal[2]['source_manifest_sha256'])
                rows=json.loads(records[0].read_bytes())['trials']
                self.assertEqual(hashed(rows),binding['schedule_sha256'])
                self.assertIn(binding['scheduled_trial_sha256'],[hashed(row) for row in rows])
                observed.append(binding['trial_id'])
            return original(*args)
        self.transport=transport
        code,result=self.invoke(runtime,pin,self.root/'output')
        self.assertEqual(code,3,result)
        self.assertTrue(observed)
        self.assertEqual(len(set(observed)),12)
        self.assertFalse(result['automatic_promotion'])
        store=HistoryStore(self.root/'output/private-history');run=result['controller_run_id']
        case=store.checked(run)[0]
        target=case/'realized-schedule.json';original=target.read_bytes()
        try:
            changed=json.loads(original);changed['trials'][0]['trial_id']='forged'
            target.write_text(json.dumps(changed))
            with self.assertRaises(ValueError):store.checked(run)
        finally:target.write_bytes(original)
        self.assertEqual(len(set(observed)),12)
        store.checked(run)


if __name__ == '__main__': unittest.main()
