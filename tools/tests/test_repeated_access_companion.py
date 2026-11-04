"""Synthetic companion integration; repeated plan math is conditional, never certification."""
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_harness_lab.evaluation import repeated_workflow,certification_access
from agent_harness_lab.evaluation.oracle_history import HistoryStore
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.private_journal import encoded,record
import test_repeated_workflow as fixtures
import test_sandbox_kernel as kernel


class RepeatedAccessAdmissionTests(fixtures.PublicFixture,unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.cgroup_root=self.root/'no-cgroup'; self.configure()
        self.selected=self.root/'selected-history'

    def arguments(self,*args):
        return super().arguments(*args)+['--evaluation-store',str(self.selected)]

    def request_args(self,selected):
        return SimpleNamespace(agent='responses',command='improve',experiment_request=self.request,
            inference_settings=self.settings,project=self.project,image_root=self.root/'image',
            evaluation_store=selected,cgroup_root=self.cgroup_root,image_sha256='a'*64,disk_bytes=64*1024*1024)

    def test_request_uses_the_explicit_same_store_instead_of_a_new_output_store(self):
        output=self.root/'prepared'; output.mkdir()
        actual=repeated_workflow.Request(self.request_args(self.selected),output,time.monotonic()+30)
        self.assertEqual(actual.store,self.selected)
        self.assertEqual(self.calls,[]); self.assertEqual(self.key_reads,0)

    def test_selected_store_inside_fixed_bank_rejects_before_writing_analyzed_inputs(self):
        self.selected=self.bank/'must-not-be-created'; before=digest(self.bank)
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',side_effect=AssertionError('no invalid-store probe')):
            with self.assertRaises(SystemExit): self.invoke(self.root/'image','a'*64,self.root/'output')
        self.assertFalse(self.selected.exists(),'preflight wrote controller metadata inside the declared fixed bank')
        self.assertEqual(digest(self.bank),before)
        self.assertEqual(self.calls,[]); self.assertEqual(self.key_reads,0)

    def test_default_request_keeps_its_existing_local_history(self):
        output=self.root/'prepared';output.mkdir();args=self.request_args(None)
        self.assertEqual(repeated_workflow.Request(args,output,time.monotonic()+30).store,output/'private-history')

    def test_changed_request_cannot_repin_selected_store_preflight(self):
        args=self.request_args(self.selected)
        _,args._evaluation_request_sha256=repeated_workflow.selected_store(args)
        self.request_value['seed']+=1;self.request.write_bytes(encoded(self.request_value))
        with self.assertRaisesRegex(ValueError,'request changed'):
            repeated_workflow.Request(args,self.root/'output',time.monotonic()+30)
        self.assertFalse(self.selected.exists())

    def test_declared_owner_runtime_and_project_cannot_hold_selected_store(self):
        source=record(self.manifest)[0]
        owner=self.root/'owned-bank'
        source['bank_authority']={'bank_path':str(owner/'revisions/one/bank.json')}
        self.manifest.write_bytes(encoded(source));self.request_value['manifest_sha256']=record(self.manifest)[1]
        self.request.write_bytes(encoded(self.request_value))
        for selected in (owner/'history',self.root/'image/history',self.project/'history'):
            with self.subTest(selected=selected),self.assertRaises(ValueError):
                repeated_workflow.selected_store(self.request_args(selected))
            self.assertFalse(selected.exists())

    def test_explicit_shared_trial_limit_does_not_rewrite_frozen_plan(self):
        output=self.root/'prepared';output.mkdir();args=self.request_args(self.selected)
        args.project_settings=self.root/'declared-config';args.trial_limit=12
        request=repeated_workflow.Request(args,output,time.monotonic()+30)
        self.assertEqual(request.blocked_reason,'configured_shared_trial_budget_infeasible')
        self.assertEqual((request.template.repeats,request.template.max_trials),(2,12))
        self.assertEqual((self.calls,self.key_reads),([],0))


@unittest.skipUnless(kernel.ENABLED,'explicit delegated kernel profile required')
class RepeatedAccessKernelTests(fixtures.PublicFixture,kernel.KernelFixture):
    def setUp(self):
        super().setUp();self.configure();self.selected=self.root/'selected-history'
        # Known recipe variants share one root; successful artifacts do not supply independent roots.
        value=record(self.manifest)[0]
        for task in value['tasks']:task['root']='shared-synthetic-recipe'
        self.manifest.write_bytes(encoded(value));self.request_value.update(
            manifest_sha256=record(self.manifest)[1],bank_sha256=digest(self.bank))
        self.request.write_bytes(encoded(self.request_value))

    def arguments(self,*args):
        return super().arguments(*args)+['--evaluation-store',str(self.selected)]

    def test_canonical_repeated_uses_one_store_without_independence_upgrade(self):
        runtime=self.root/'runtime';pin=kernel.pinned_runtime(runtime)['sha256']
        code,result=self.invoke(runtime,pin,self.root/'output')
        self.assertEqual(code,3,result)
        self.assertNotEqual(result['recommendation'],'review_candidate',result)
        self.assertEqual(result['analysis']['status'],'inconclusive',result)
        self.assertEqual(result['analysis']['roots'],1)
        self.assertFalse((self.root/'output/private-history').exists())
        checked=HistoryStore(self.selected).checked(result['controller_run_id'])
        self.assertEqual(len(checked[4]['trials']),12)
        for row in checked[4]['trials']:
            self.assertEqual(row['grader']['outcome'],row['version']=='candidate')
        state=certification_access._state(HistoryStore(self.selected).access_ledger().path)
        self.assertEqual(len(state['constructions']),1)
        self.assertTrue(state['observed_assets'])
        self.assertFalse(result['automatic_promotion'])

    def test_request_changed_during_proposer_cannot_gain_a_sealed_comparison(self):
        runtime=self.root/'runtime';pin=kernel.pinned_runtime(runtime)['sha256']
        original=self.transport
        def changed(*args):
            reply=original(*args)
            self.request_value['seed']+=1
            self.request.write_bytes(encoded(self.request_value))
            return reply
        self.transport=changed
        with self.assertRaises(SystemExit) as failure:
            self.invoke(runtime,pin,self.root/'output')
        self.assertEqual(failure.exception.code,2)
        result=record(self.root/'output/workflow.json')[0]
        self.assertEqual(result['status'],'invalid_input',result)
        self.assertEqual(len(self.calls),2)
        self.assertNotIn('controller_run_id',result)
        state=certification_access._state(HistoryStore(self.selected).access_ledger().path)
        self.assertTrue(state['constructions'])
        self.assertTrue(all(row['state']!='complete' for row in state['constructions'].values()))
