"""Prospective removal with real workers and two frozen synthetic response controls.

Each native case creates fresh signed authorities. Neither response control proves
real model reasoning or universal benefit; both use only actual serialized input.
"""
from contextlib import redirect_stdout,redirect_stderr
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_harness_lab import conditional_application as application
from agent_harness_lab.cli import main
from agent_harness_lab.evaluation import responses
from agent_harness_lab.evaluation.instruction_context import MARKER
from agent_harness_lab.evaluation.oracle_history import HistoryStore
if __package__:
    from . import test_conditional_application_native as fixture
else:
    import test_conditional_application_native as fixture


REMOVAL_POLICY='controller-agents-remove-conditional-apply-v1'
USEFUL_INSTRUCTION=b'good\nPreserve the useful fixed-transform rule.\n'
REMOVE_PROPOSER='''from pathlib import Path
import json
Path('candidate/AGENTS.md').unlink()
Path('proposal.json').write_text(json.dumps(dict(status='candidate',
 mechanisms=['remove conflicting root instruction'],falsifiers=['fixed final regression'])))
'''


def removal_behavior(request,context):
    """Use actual serialized membership and task text; no version/arm argument."""
    return 'good' if context['membership']=='absent' else 'bad'


def useful_rule_behavior(request,context):
    """Predeclared control: removing the rule helps development but harms final."""
    final=any(prompt in request['input'][1]['content'] for _,prompt,_,_ in fixture.TEN_CASES)
    if not final:return removal_behavior(request,context)
    return 'good' if context['membership']=='present' and context['content']==USEFUL_INSTRUCTION.decode() else 'bad'


class RemovalDesignTests(unittest.TestCase):
    def test_removal_worker_deletes_candidate_only_and_emits_existing_proposal_schema(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);candidate=root/'candidate';candidate.mkdir()
            (root/'AGENTS.md').write_bytes(USEFUL_INSTRUCTION)
            (candidate/'AGENTS.md').write_bytes(USEFUL_INSTRUCTION)
            subprocess.run([sys.executable,'-c',REMOVE_PROPOSER],cwd=root,check=True,timeout=5,
                stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            self.assertFalse((candidate/'AGENTS.md').exists())
            self.assertEqual((root/'AGENTS.md').read_bytes(),USEFUL_INSTRUCTION)
            proposal=json.loads((root/'proposal.json').read_bytes())
            self.assertEqual(set(proposal),{'status','mechanisms','falsifiers'})
            self.assertEqual(proposal['status'],'candidate')

    def test_membership_and_fixed_prompt_drive_prospective_positive_and_regression(self):
        request={'input':[{'content':'synthetic developer'},{'content':'Produce the synthetic result'}]}
        absent=dict(membership='absent',content='')
        bad=dict(membership='present',content='bad\n')
        useful=dict(membership='present',content=USEFUL_INSTRUCTION.decode())
        self.assertEqual([removal_behavior(request,context) for context in (bad,useful,absent)],['bad','bad','good'])
        request['input'][1]['content']=fixture.TEN_CASES[0][1]
        self.assertEqual([removal_behavior(request,context) for context in (bad,useful,absent)],['bad','bad','good'])
        self.assertEqual([useful_rule_behavior(request,context) for context in (bad,useful,absent)],['bad','good','bad'])
        request['input'][1]['content']='Produce the synthetic result'
        self.assertEqual([useful_rule_behavior(request,context) for context in (bad,useful,absent)],['bad','bad','good'])
        self.assertIs(RemovalKernelTests.FINAL_METHOD,fixture.TEN_METHOD)
        self.assertIs(RemovalKernelTests.WORKER,fixture.TEN_EXECUTOR)
        self.assertEqual(RemovalKernelTests.TRIAL_LIMIT,fixture.TenRootConditionalApplicationKernelTests.TRIAL_LIMIT)

    def test_native_selection_does_not_duplicate_inherited_matrix(self):
        loader=unittest.TestLoader()
        selected=load_tests(loader,None,None)
        self.assertEqual(selected.countTestCases(),5)
        self.assertEqual({name for name in loader.getTestCaseNames(RemovalKernelTests)
            if name in RemovalKernelTests.__dict__},
            {'test_removal_applies_absence_consumes_replay_and_rollback_restores',
             'test_useful_rule_removal_regresses_same_final_and_keeps_target'})


class RemovalKernelTests(fixture.TenRootConditionalApplicationKernelTests):
    APPLICATION_POLICY=REMOVAL_POLICY
    behavior=staticmethod(removal_behavior)

    def transport(self,credential,body,*args):
        request=json.loads(body);developer=request['input'][0]['content']
        if 'You are the proposer' in developer:
            action=json.loads(developer.split(application.ACTION_MARKER,1)[1])
            self.assertEqual(action,dict(policy=REMOVAL_POLICY,path='AGENTS.md',action='remove'))
        return super().transport(credential,body,*args)

    def proposer_argv(self):
        return ['/usr/bin/python3','-c',REMOVE_PROPOSER]

    def executor_argv(self,request,context):
        self.assertIn(context['membership'],('present','absent'))
        if context['membership']=='absent':self.assertEqual(context['content'],'')
        return ['/usr/bin/python3','-c',self.WORKER,self.behavior(request,context)]

    def target(self):
        path=self.project/'AGENTS.md'
        return (path.read_bytes(),path.stat().st_mode&0o777) if path.exists() else None

    def final_rows(self):
        self.assert_final_spent()
        reservation=next(iter(self.reservations().values()))
        case=HistoryStore(self.root/'history').case(reservation['run_id'])
        rows=json.loads((case/'execution/results.json').read_bytes())['trials']
        self.assertEqual(len(rows),fixture.TEN_METHOD['max_trials'])
        self.assertEqual({row['task'] for row in rows},self.FINAL_TASKS)
        self.assertEqual(len(self.calls),56)
        self.assertEqual({context['membership'] for context in self.instruction_contexts},{'present','absent'})
        for request in self.calls:
            developer=request['input'][0]['content']
            if MARKER in developer:
                context=json.loads(developer.split(MARKER,1)[1])
                if context['membership']=='absent':self.assertEqual(context['content'],'')
        return rows

    def test_removal_applies_absence_consumes_replay_and_rollback_restores(self):
        self.setup_authorities();before=self.target()
        observed,code,result,_,_=self.invoke_negative()
        self.assertIsNone(observed)
        self.assertEqual((code,result['status'],result['conditional_claim']),(0,'applied',REMOVAL_POLICY),result)
        self.assertEqual(result['changed_files'],['AGENTS.md']);self.assertFalse(result['general_certification'])
        records,active=fixture.promotion_inventory(self.root/'state',self.project)
        self.assertTrue(active);self.assertEqual(len(records),1)
        self.assertEqual((records[0]['deployment'],records[0]['status']),(result['deployment'],'committed'))
        self.assertEqual(records[0]['after'],{'AGENTS.md':None})
        rows=self.final_rows()
        self.assertTrue(all(row['status']==('passed' if row['version']=='candidate' else 'failed') for row in rows))
        calls=len(self.calls);events=self.store.opened(self.authorization)[2]
        with patch.dict(os.environ,{'XDG_STATE_HOME':str(self.root/'state')}), \
                patch.object(responses,'read_credential',side_effect=AssertionError('consumed credential read')), \
                patch.object(responses,'request',side_effect=AssertionError('consumed replay')), \
                redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):main(['improve',str(self.project)])
        self.assertIsNone(self.target());self.assertEqual(len(self.calls),calls)
        self.assertEqual(self.store.opened(self.authorization)[2][-1][1],events[-1][1])
        with patch.dict(os.environ,{'XDG_STATE_HOME':str(self.root/'state')}),redirect_stdout(io.StringIO()):
            self.assertEqual(main(['rollback',result['deployment'],'--project',str(self.project)]),0)
        self.assertEqual(self.target(),before)

    def test_useful_rule_removal_regresses_same_final_and_keeps_target(self):
        self.INITIAL_INSTRUCTION=USEFUL_INSTRUCTION
        self.behavior=useful_rule_behavior
        self.setup_authorities();before=self.target()
        observed,code,result,_,_=self.invoke_negative()
        self.assertEqual(observed,before)
        self.assertEqual((code,result['status'],result['reason']),(3,'inconclusive','conditional_final_not_supported'),result)
        rows=self.final_rows()
        self.assertTrue(all(row['status']==('failed' if row['version']=='candidate' else 'passed') for row in rows))
        self.assert_spent(56)


def load_tests(loader,tests,pattern):
    # Reuse fixture methods without rediscovering its inherited native matrix.
    selected=unittest.TestSuite([loader.loadTestsFromTestCase(RemovalDesignTests)])
    selected.addTests(RemovalKernelTests(name) for name in loader.getTestCaseNames(RemovalKernelTests)
        if name in RemovalKernelTests.__dict__)
    return selected
