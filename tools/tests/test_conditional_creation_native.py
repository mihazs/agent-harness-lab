"""Prospective creation using three frozen synthetic transport response controls.

Fresh signed setup per case; real workers never imply real model understanding
or universal benefit. All behavior uses actual serialized instructions/task text.
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
if __package__:
    from . import test_conditional_application_native as fixture,test_conditional_removal_native as removal
else:
    import test_conditional_application_native as fixture
    import test_conditional_removal_native as removal


CREATION_POLICY='controller-agents-create-conditional-apply-v1'
CREATED_INSTRUCTION=b'good\nUse the fixed synthetic artifact transformation rule.\n'
CREATED_MODE=0o600
CREATE_PROPOSER='''from pathlib import Path
import json
target=Path('candidate/AGENTS.md')
if target.exists() or target.is_symlink():raise ValueError('candidate instruction must be absent')
target.write_bytes(%r)
target.chmod(%r)
Path('proposal.json').write_text(json.dumps(dict(status='candidate',
 mechanisms=['create missing root instruction'],falsifiers=['fixed final regression'])))
''' % (CREATED_INSTRUCTION,CREATED_MODE)


def creation_behavior(request,context):
    return 'good' if context['membership']=='present' and context['content']==CREATED_INSTRUCTION.decode() else 'bad'


def equal_behavior(request,context):
    final=any(prompt in request['input'][1]['content'] for _,prompt,_,_ in fixture.TEN_CASES)
    return 'good' if final else creation_behavior(request,context)


def harmful_behavior(request,context):
    final=any(prompt in request['input'][1]['content'] for _,prompt,_,_ in fixture.TEN_CASES)
    if not final:return creation_behavior(request,context)
    return 'good' if context['membership']=='absent' else 'bad'


class CreationDesignTests(unittest.TestCase):
    def test_worker_creates_candidate_only_with_exact_bytes_mode_and_existing_schema(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'candidate').mkdir()
            subprocess.run([sys.executable,'-c',CREATE_PROPOSER],cwd=root,check=True,timeout=5,
                stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            self.assertFalse((root/'AGENTS.md').exists())
            target=root/'candidate/AGENTS.md'
            self.assertEqual((target.read_bytes(),target.stat().st_mode&0o777),(CREATED_INSTRUCTION,CREATED_MODE))
            proposal=json.loads((root/'proposal.json').read_bytes())
            self.assertEqual(set(proposal),{'status','mechanisms','falsifiers'});self.assertEqual(proposal['status'],'candidate')

    def test_predeclared_transports_depend_on_actual_membership_content_and_task(self):
        request={'input':[{'content':'synthetic developer'},{'content':'Produce the synthetic result'}]}
        contexts=(dict(membership='absent',content=''),dict(membership='present',content=CREATED_INSTRUCTION.decode()))
        for behavior in (creation_behavior,equal_behavior,harmful_behavior):
            self.assertEqual([behavior(request,context) for context in contexts],['bad','good'])
        request['input'][1]['content']=fixture.TEN_CASES[0][1]
        self.assertEqual([creation_behavior(request,context) for context in contexts],['bad','good'])
        self.assertEqual([equal_behavior(request,context) for context in contexts],['good','good'])
        self.assertEqual([harmful_behavior(request,context) for context in contexts],['good','bad'])
        self.assertIs(CreationKernelTests.INITIAL_INSTRUCTION,None)
        self.assertIs(CreationKernelTests.FINAL_METHOD,fixture.TEN_METHOD)
        self.assertIs(CreationKernelTests.WORKER,fixture.TEN_EXECUTOR)
        self.assertEqual(CreationKernelTests.TRIAL_LIMIT,fixture.TenRootConditionalApplicationKernelTests.TRIAL_LIMIT)

    def test_selection_includes_only_three_new_native_cases(self):
        loader=unittest.TestLoader();suite=load_tests(loader,None,None)
        self.assertEqual(suite.countTestCases(),6)
        self.assertEqual(len([name for name in loader.getTestCaseNames(CreationKernelTests)
            if name in CreationKernelTests.__dict__]),3)


class CreationKernelTests(removal.RemovalKernelTests):
    APPLICATION_POLICY=CREATION_POLICY
    INITIAL_INSTRUCTION=None
    behavior=staticmethod(creation_behavior)

    def allocate_authorization(self):
        self.assertIsNone(self.target())
        output=io.StringIO()
        with redirect_stdout(output):
            code=main(['application-review','--store',str(self.store.root),'allocate',str(self.project),'--operation','create'])
        self.assertEqual(code,0);ident=json.loads(output.getvalue())['authorization_id']
        _,inputs,_=self.store.opened(ident)
        self.assertEqual(inputs['baseline'],{'membership':'absent'})
        self.assertEqual(inputs['profile'],'controller-instruction-creation-authorization-v1')
        self.assertIsNone(self.target())
        return ident

    def transport(self,credential,body,*args):
        developer=json.loads(body)['input'][0]['content']
        if 'You are the proposer' in developer:
            action=json.loads(developer.split(application.ACTION_MARKER,1)[1])
            self.assertEqual(action,dict(policy=CREATION_POLICY,path='AGENTS.md',action='create'))
        return fixture.ConditionalApplicationKernelTests.transport(self,credential,body,*args)

    def proposer_argv(self):
        return ['/usr/bin/python3','-c',CREATE_PROPOSER]

    def test_creation_applies_exact_file_consumes_replay_and_rollback_restores_absence(self):
        self.setup_authorities();self.assertIsNone(self.target())
        observed,code,result,_,_=self.invoke_negative()
        self.assertEqual(observed,(CREATED_INSTRUCTION,CREATED_MODE))
        self.assertEqual((code,result['status'],result['conditional_claim']),(0,'applied',CREATION_POLICY),result)
        self.assertEqual(result['changed_files'],['AGENTS.md']);self.assertFalse(result['general_certification'])
        records,active=fixture.promotion_inventory(self.root/'state',self.project)
        self.assertTrue(active);self.assertEqual(len(records),1)
        self.assertEqual(records[0]['before'],{'AGENTS.md':None})
        self.assertEqual((records[0]['deployment'],records[0]['status']),(result['deployment'],'committed'))
        rows=self.final_rows()
        self.assertTrue(all(row['status']==('passed' if row['version']=='candidate' else 'failed') for row in rows))
        calls=len(self.calls);event=self.store.opened(self.authorization)[2][-1][1]
        with patch.dict(os.environ,{'XDG_STATE_HOME':str(self.root/'state')}), \
                patch.object(responses,'read_credential',side_effect=AssertionError('consumed creation credential read')), \
                patch.object(responses,'request',side_effect=AssertionError('consumed creation replay')), \
                redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):main(['improve',str(self.project)])
        self.assertEqual(self.target(),observed);self.assertEqual(len(self.calls),calls)
        self.assertEqual(self.store.opened(self.authorization)[2][-1][1],event)
        with patch.dict(os.environ,{'XDG_STATE_HOME':str(self.root/'state')}),redirect_stdout(io.StringIO()):
            self.assertEqual(main(['rollback',result['deployment'],'--project',str(self.project)]),0)
        self.assertIsNone(self.target())

    def assert_rejected_creation(self,status):
        self.setup_authorities();self.assertIsNone(self.target())
        observed,code,result,_,_=self.invoke_negative()
        self.assertIsNone(observed)
        self.assertEqual((code,result['status'],result['reason']),(3,'inconclusive','conditional_final_not_supported'),result)
        rows=self.final_rows()
        self.assertTrue(all(row['status']==status(row['version']) for row in rows))
        self.assert_spent(56)

    def test_equal_final_creation_preserves_absence_and_spent_access(self):
        self.behavior=equal_behavior
        self.assert_rejected_creation(lambda version:'passed')

    def test_harmful_creation_regresses_final_preserves_absence_and_spent_access(self):
        self.behavior=harmful_behavior
        self.assert_rejected_creation(lambda version:'failed' if version=='candidate' else 'passed')


def load_tests(loader,tests,pattern):
    selected=unittest.TestSuite([loader.loadTestsFromTestCase(CreationDesignTests)])
    selected.addTests(CreationKernelTests(name) for name in loader.getTestCaseNames(CreationKernelTests)
        if name in CreationKernelTests.__dict__)
    return selected
