"""Signed conditional application through public improve and native isolated roles."""
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

import agent_harness_lab
from agent_harness_lab import conditional_application as application, config
from agent_harness_lab.cli import main
from agent_harness_lab.evaluation import bank_qualification as bankq, bank_requalification as renewal
from agent_harness_lab.evaluation import responses, review_authority as authority
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.instruction_context import MARKER, PROFILE as INSTRUCTION_PROFILE
from agent_harness_lab.evaluation.oracle_history import HistoryStore
from agent_harness_lab.evaluation.plan import ExperimentSpec
from agent_harness_lab.evaluation.precision import preflight
from agent_harness_lab.evaluation.regression_review import ReviewStore
from agent_harness_lab.private_journal import encoded
if __package__:
    from . import test_bank_qualification_final as bank_fixture
    from . import test_sandbox_construction as roles, test_sandbox_inference as wire, test_sandbox_kernel as kernel
    from .test_offline_evaluation import SOLVER
else:
    import test_bank_qualification_final as bank_fixture
    import test_sandbox_construction as roles
    import test_sandbox_inference as wire
    import test_sandbox_kernel as kernel
    from test_offline_evaluation import SOLVER


EXECUTOR = '''from pathlib import Path
import json,sys
task=json.loads(Path('task.json').read_text())
good=sys.argv[1]=='good'
if 'scenario' in task:
 Path('result.json').write_text(json.dumps(dict(complete=good or task['split']=='regression',scenario=task['scenario'])))
elif good:
 exec(%r)
else:
 Path('result.json').write_text('{}')
''' % SOLVER


@unittest.skipUnless(kernel.ENABLED, 'explicit delegated native scope required')
class ConditionalApplicationKernelTests(kernel.KernelFixture):
    FINAL_METHOD=dict(experiment_id='synthetic-conditional-final', repeats=1, seed=7, max_trials=4, min_roots=2,
        effect_margin=.01, family_margin=.01, alpha=.99, family_weights={'regression':.5, 'tool-recovery':.5}, precision_half_width=None)
    TRIAL_LIMIT=24
    FINAL_TASKS={'regression', 'tool-recovery'}
    FEASIBLE=False
    WORKER=EXECUTOR

    def sign(self, path, namespace):
        subprocess.run(['/usr/bin/ssh-keygen', '-Y', 'sign', '-f', str(self.key), '-n', namespace, str(path)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=5)
        return Path(str(path)+'.sig')

    def policy(self, name, purpose):
        path=self.operator/(name+'.json')
        path.write_bytes(encoded(dict(schema_version=1, profile=authority.POLICY, purpose=purpose,
            reviewers={'synthetic-reviewer':self.public_key})))
        path.chmod(0o600)
        return path

    def reviewed_bank(self):
        original=Path(agent_harness_lab.__file__).parent/'evaluation/fixtures/starter-v1'
        source=self.root/'final-source';source.mkdir()
        declared=json.loads((original/'bank.json').read_bytes())
        tasks=[]
        for task in declared['tasks']:
            if task['task_id'] not in ('regression', 'tool-recovery'):continue
            task=dict(task)
            shutil.copytree(original/task['fixture'], source/task['fixture'])
            bundle=source/task['grader_bundle'];shutil.copytree(original/task['grader_bundle'], bundle)
            shutil.copytree(bundle/'controls/complete', bundle/'controls/superficial')
            (bundle/'controls/superficial/original.txt').write_text('damaged\n')
            (bundle/'controls.json').write_bytes(encoded_controls())
            task.update(partition='certification', review_status='accepted', independence='reviewed',
                reviewer='synthetic-reviewer', grader_digest=digest(bundle))
            tasks.append(task)
        self.assertEqual({task['root_id'] for task in tasks}, {'calculator','recovery'})
        bank=source/'bank.json';bank.write_bytes(encoded(dict(schema_version=1, separate_by=['root_id'], tasks=tasks)))
        return self.qualify_bank(bank,tasks)

    def qualify_bank(self,bank,tasks):
        declaration=self.root/'bank-declaration.json'
        declaration.write_bytes(encoded(dict(schema_version=1, author='synthetic-author', history_store=str(self.root/'history'),
            protocol=bankq.PROTOCOL, judgments={task['task_id']:dict(source_permission='approved', lineage='reviewed',
                split_separation='reviewed', evidence_refs=[dict(ref='synthetic:public-fixed-task', sha256='a'*64)]) for task in tasks},
            external_exposure='unknown', pretraining_exposure='unknown')))
        code,_=bank_fixture.invoke(self.store.root, 'trust-reviewers', '--policy', self.policy('bank', authority.BANK_PURPOSE))
        self.assertEqual(code, 0)
        code,intake=bank_fixture.invoke(self.store.root, 'intake', '--bank', bank, '--declaration', declaration)
        self.assertEqual(code, 0)
        case_id=intake['case_id']
        code,audited=bank_fixture.invoke(self.store.root, 'audit', '--case', case_id,
            '--cgroup-root', self.cgroup_root, '--image-root', self.runtime, '--image-sha256', self.image,
            '--disk-bytes', 64*1024*1024, '--budget-seconds', 120 if self.FEASIBLE else 60)
        self.assertEqual((code, audited and audited['state']), (0, 'bank_audited'))
        code,prepared=bank_fixture.invoke(self.store.root, 'prepare-review', '--case', case_id, '--reviewer', 'synthetic-reviewer')
        self.assertEqual(code, 0)
        request=Path(prepared['request_path']);signature=self.sign(request, 'ahl.final-bank-review.v1')
        code,qualified=bank_fixture.invoke(self.store.root, 'qualify', '--case', case_id, '--request', request, '--signature', signature)
        self.assertEqual((code, qualified and qualified['qualification']), (0, 'qualified_under_protocol'))
        return case_id

    def setup_authorities(self, *, max_requests=60):
        self.runtime=self.root/'runtime';self.image=kernel.pinned_runtime(self.runtime)['sha256']
        self.operator=self.root/'operator';self.operator.mkdir(mode=0o700);self.key=self.operator/'reviewer'
        subprocess.run(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'synthetic', '-f', str(self.key)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=5)
        self.public_key=' '.join(self.key.with_suffix('.pub').read_text().split()[:2])
        self.store=ReviewStore(self.root/'review');case_id=self.reviewed_bank()
        self.project=self.root/'project';self.project.mkdir();(self.project/'AGENTS.md').write_text('bad\n')
        authorization=application.allocate(self.store, self.project)['authorization_id'];self.authorization=authorization
        locator=renewal.allocate_delegation(self.store)
        credential=self.root/'synthetic-unused-token';credential.write_text('SYNTHETIC_UNUSED');credential.chmod(0o600)
        self.settings=self.root/'settings.json'
        self.settings.write_bytes(encoded(dict(schema_version=1, model='synthetic-controlled', credential_file=str(credential),
            max_requests=max_requests, max_tool_calls=40, max_output_tokens=100, max_request_bytes=65536, max_response_bytes=32768,
            instruction_protocol=INSTRUCTION_PROFILE)))
        value=config.prepare(dict(schema_version=2, profile='cooperative', recommendation_only=False, harness_paths=['AGENTS.md'],
            runtime=dict(name='responses', inference_settings=str(self.settings), cgroup_root=str(self.cgroup_root),
                image_root=str(self.runtime), image_sha256=self.image, disk_bytes=64*1024*1024, permission_audit='permission-results-v1'),
            limits=dict(seconds=150, trials=self.TRIAL_LIMIT), application=dict(policy=application.POLICY, review_store=str(self.store.root),
                authorization_id=authorization, bank_case_id=case_id, delegation_request=locator['request_path'],
                delegation_signature=locator['signature_path'], method=self.FINAL_METHOD)))
        config.create(self.project, value)
        events=self.root/'events.jsonl';events.write_text('')
        deadline=time.monotonic()+240
        renewal.install_policy(self.store, self.policy('delegation', renewal.PURPOSE))
        packet_output=io.StringIO()
        with redirect_stdout(packet_output):
            code=main(['bank-review','--store',str(self.store.root),'prepare-delegation','--project',str(self.project),
                '--events',str(events),'--format','normalized','--reviewer','synthetic-reviewer','--budget-seconds','240'])
        self.assertEqual(code,0)
        prepared=json.loads(packet_output.getvalue())
        self.sign(Path(prepared['request_path']), 'ahl.final-bank-requalification.v1')
        application.install_policy(self.store, self.policy('application', application.PURPOSE))
        current,pin=config.load(self.project/'ahl.json');options=config.execution_options(self.project, current, {}, set())
        prepared=application.prepare(self.store, authorization, self.project, current, pin, options, 'synthetic-reviewer')
        request=self.store.root/'review-requests'/(prepared['request_sha256']+'.json')
        signature=self.sign(request, 'ahl.instruction-application.v1')
        application.authorize(self.store, authorization, request, signature, deadline=deadline)
        self.calls=[];self.instruction_contexts=[]

    def transport(self, credential, body, *args):
        self.assertIsNone(credential);request=json.loads(body);self.calls.append(request);ident=str(len(self.calls))
        developer=request['input'][0]['content']
        if 'You are the proposer' in developer:
            self.assertNotIn(MARKER, developer);argv=self.proposer_argv()
        elif 'You are the evaluator author' in developer:
            self.assertNotIn(MARKER, developer);argv=self.author_argv()
        else:
            context=json.loads(developer.split(MARKER, 1)[1]);self.instruction_contexts.append(context)
            self.assertEqual(context['profile'], INSTRUCTION_PROFILE)
            argv=self.executor_argv(request,context)
        if any(row.get('type')=='function_call_output' for row in request['input']):
            return wire.reply([wire.message('final-'+ident)])
        return wire.reply([wire.call(ident, arguments=json.dumps(dict(argv=argv)))])

    def proposer_argv(self):
        return ['/usr/bin/python3','-c',roles.PROPOSER,'good']

    def author_argv(self):
        return ['/usr/bin/python3','-c',roles.AUTHOR,'1','complete']

    def executor_argv(self,request,context):
        return ['/usr/bin/python3','-c',self.WORKER,'good' if context['content'].startswith('good') else 'bad']

    def test_plain_improve_consumes_signed_final_then_applies_and_public_rollback_restores(self):
        self.setup_authorities();self.assertNotIn('AGENTS.md', self.WORKER)
        initial_target=((self.project/'AGENTS.md').read_bytes(),(self.project/'AGENTS.md').stat().st_mode&0o777)
        registries=(application._SESSIONS,application._FINAL_RUNS,application._GRANTS)
        initial_registry_counts=tuple(len(registry) for registry in registries)
        output=io.StringIO()
        with patch.dict(os.environ, {'XDG_STATE_HOME':str(self.root/'state')}), \
                patch.object(responses, 'read_credential', return_value=None), \
                patch.object(responses, 'request', side_effect=self.transport), \
                redirect_stdout(output), redirect_stderr(io.StringIO()):
            code=main(['improve', str(self.project)])
        self.assertEqual(tuple(len(registry) for registry in registries),initial_registry_counts)
        result=json.loads(output.getvalue())
        observed_target=((self.project/'AGENTS.md').read_bytes(),(self.project/'AGENTS.md').stat().st_mode&0o777)
        if code!=0:self.assertEqual(observed_target,initial_target,result)
        else:self.assertEqual(observed_target,(b'good\nSYNTHETIC_CANDIDATE_SECRET\n',initial_target[1]),result)
        if not self.FEASIBLE:
            self.assertEqual((code,result['status'],result['reason']), (3,'inconclusive','precision_target_infeasible'),result)
            self.assertEqual(result['precision_preflight']['status'],'infeasible')
            self.assertNotIn('controller_run_id',result)
            self.assertEqual((self.project/'AGENTS.md').read_text(),'bad\n')
            self.assertEqual(len(self.calls),0)
            return
        self.assertEqual((code, result['status']), (0, 'applied'), result)
        self.assertEqual((self.project/'AGENTS.md').read_text(), 'good\nSYNTHETIC_CANDIDATE_SECRET\n')
        self.assertEqual(result['conditional_claim'], application.POLICY)
        self.assertFalse(result['general_certification']);self.assertFalse(result['automatic_promotion'])
        self.assertEqual(result['served_model'], 'unverified');self.assertEqual(result['native_activation'], 'unverified')
        self.assertEqual(result['external_exposure'], 'unknown');self.assertEqual(result['pretraining_exposure'], 'unknown')
        self.assertEqual(result['analysis']['status'], 'supported')
        self.assertEqual(len(self.calls),56)
        contents=[context['content'] for context in self.instruction_contexts]
        self.assertIn('bad\n', contents);self.assertIn('good\nSYNTHETIC_CANDIDATE_SECRET\n', contents)
        executor_requests=[request for request in self.calls if MARKER in request['input'][0]['content']]
        for content in ('bad\n', 'good\nSYNTHETIC_CANDIDATE_SECRET\n'):
            selected=[request for request in executor_requests
                if json.loads(request['input'][0]['content'].split(MARKER, 1)[1])['content']==content]
            self.assertTrue(any(not any(row.get('type')=='function_call_output' for row in request['input']) for request in selected))
            self.assertTrue(any(any(row.get('type')=='function_call_output' for row in request['input']) for request in selected))
        final=HistoryStore(self.root/'history').case(result['controller_run_id'])
        trials=json.loads((final/'execution/results.json').read_bytes())['trials']
        self.assertEqual(len(trials), self.FINAL_METHOD['max_trials'])
        self.assertEqual({trial['task'] for trial in trials}, self.FINAL_TASKS)
        self.assertTrue(all(trial['status']==('passed' if trial['version']=='candidate' else 'failed') for trial in trials))
        before=len(self.calls)
        with patch.dict(os.environ, {'XDG_STATE_HOME':str(self.root/'state')}), \
                patch.object(responses, 'read_credential', side_effect=AssertionError('consumed approval must not read credentials')), \
                patch.object(responses, 'request', side_effect=AssertionError('consumed approval must not retry')), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):main(['improve', str(self.project)])
        self.assertEqual(len(self.calls), before)
        with patch.dict(os.environ, {'XDG_STATE_HOME':str(self.root/'state')}), redirect_stdout(io.StringIO()):
            self.assertEqual(main(['rollback', result['deployment'], '--project', str(self.project)]), 0)
        self.assertEqual((self.project/'AGENTS.md').read_text(), 'bad\n')


def encoded_controls():
    return (json.dumps([dict(state=state, fixture='controls/'+state)
        for state in ('broken','partial','complete','alternative_valid','superficial')], sort_keys=True)+'\n').encode()


TEN_CASES=(
    ('sum_numbers','Add the signed integer operands and return their total.',{'values':[4,-2,9]},11),
    ('reverse_words','Reverse the ordered sequence of words without changing their spelling.',{'text':'maple quartz river'},['river','quartz','maple']),
    ('sort_values','Sort the integer measurements in ascending numerical order.',{'values':[8,-3,5,1]},[-3,1,5,8]),
    ('unique_items','Keep the first occurrence of each tree label while retaining input order.',{'values':['larch','elm','larch','ash']},['larch','elm','ash']),
    ('count_tokens','Count whitespace separated color names in the supplied text.',{'text':'amber cobalt indigo sienna'},4),
    ('positive_filter','Retain strictly positive readings and discard zero and negative readings.',{'values':[-4,6,0,9,-1]},[6,9]),
    ('uppercase_labels','Convert every botanical label to uppercase ASCII letters.',{'values':['birch','cedar']},['BIRCH','CEDAR']),
    ('map_squares','Square each signed coordinate while preserving sequence order.',{'values':[-3,2,5]},[9,4,25]),
    ('zip_pairs','Pair each compass direction with the corresponding distance.',{'keys':['north','east','south'],'values':[2,7,4]},[['north',2],['east',7],['south',4]]),
    ('checksum','Sum the Unicode code points of the supplied uppercase identifier.',{'text':'ABC'},198),
)
TEN_METHOD=dict(experiment_id='synthetic-ten-root-conditional-final', repeats=1, seed=7, max_trials=20, min_roots=10,
    effect_margin=.01, family_margin=.01, alpha=.05, family_weights={'synthetic-artifact-transforms':1.}, precision_half_width=None)
TEN_GRADER='''from pathlib import Path
import json,sys
try:
 expected=json.loads(Path(sys.argv[2]).read_text())
 actual=json.loads((Path(sys.argv[1])/'result.json').read_text())
 outcome=(isinstance(actual,dict) and 'answer' in actual
  and json.dumps(actual['answer'],sort_keys=True)==json.dumps(expected['answer'],sort_keys=True)
  and (Path(sys.argv[1])/'original.txt').read_text()=='preserve\\n')
except (OSError,ValueError,TypeError,KeyError):outcome=False
print(json.dumps(dict(schema_version=1,valid=True,outcome=outcome,grader_id='synthetic-fixed-transforms',grader_version='v1')))
sys.exit(0 if outcome else 1)
'''
TEN_EXECUTOR='''from pathlib import Path
import json,sys
task=json.loads(Path('task.json').read_text())
good=sys.argv[1]=='good'
if 'scenario' in task:
 Path('result.json').write_text(json.dumps(dict(complete=good or task['split']=='regression',scenario=task['scenario'])))
elif good:
 values=task['inputs'];operation=task['operation']
 if operation=='sum_numbers':answer=sum(values['values'])
 elif operation=='reverse_words':answer=list(reversed(values['text'].split()))
 elif operation=='sort_values':answer=sorted(values['values'])
 elif operation=='unique_items':answer=list(dict.fromkeys(values['values']))
 elif operation=='count_tokens':answer=len(values['text'].split())
 elif operation=='positive_filter':answer=[value for value in values['values'] if value>0]
 elif operation=='uppercase_labels':answer=[value.upper() for value in values['values']]
 elif operation=='map_squares':answer=[value*value for value in values['values']]
 elif operation=='zip_pairs':answer=list(zip(values['keys'],values['values']))
 elif operation=='checksum':answer=sum(map(ord,values['text']))
 else:raise ValueError('unknown predeclared synthetic operation')
 Path('result.json').write_text(json.dumps(dict(answer=answer)))
else:Path('result.json').write_text('{}')
'''


def ten_root_bank(source):
    """New prospective inputs and frozen oracles; root independence remains an assumption."""
    source.mkdir();tasks=[]
    for ident,prompt,inputs,answer in TEN_CASES:
        fixture=source/'tasks'/ident;fixture.mkdir(parents=True)
        (fixture/'task.json').write_bytes(encoded(dict(prompt=prompt+' Write answer in result.json and preserve original.txt.',
            operation=ident,inputs=inputs)))
        (fixture/'original.txt').write_text('preserve\n')
        bundle=source/'graders'/ident;bundle.mkdir(parents=True)
        (bundle/'grade.py.txt').write_text(TEN_GRADER);(bundle/'expected.json').write_bytes(encoded(dict(answer=answer)))
        for state in ('broken','partial','complete','alternative_valid','superficial'):
            control=bundle/'controls'/state;shutil.copytree(fixture,control)
            value={} if state=='broken' else dict(answer=None if state=='partial' else answer)
            if state=='alternative_valid':value['extra']='allowed synthetic metadata'
            (control/'result.json').write_bytes(encoded(value))
            if state=='superficial':(control/'original.txt').write_text('damaged\n')
        (bundle/'controls.json').write_bytes(encoded_controls())
        tasks.append(dict(task_id=ident,root_id=ident,family='synthetic-artifact-transforms',partition='certification',
            project_group='synthetic-ten-root',time_group='prospective-v1',fixture=str(fixture.relative_to(source)),fixture_digest=digest(fixture),
            grader_bundle=str(bundle.relative_to(source)),grader_digest=digest(bundle),grader_version='v1',source_kind='synthetic',
            source_ref='synthetic:conditional-ten-root-v1',permission='approved',reviewer='synthetic-reviewer',review_status='accepted',
            independence='reviewed',safety=['Synthetic offline artifacts only; reviewed root independence is an assumption.'],artifacts=['result.json','original.txt']))
    bank=source/'bank.json';bank.write_bytes(encoded(dict(schema_version=1,separate_by=['root_id'],tasks=tasks)))
    return bank,tasks


NO_CHANGE_PROPOSER="from pathlib import Path\nPath('proposal.json').write_text('{\"status\":\"no_change\",\"reason\":\"No supported synthetic change\"}')\n"
INVALID_AUTHOR=roles.AUTHOR.replace('valid=True,outcome=outcome','valid=False,outcome=outcome')


class ConditionalFinalDesignTests(unittest.TestCase):
    def precision(self,method,tasks):
        spec=ExperimentSpec(method['experiment_id'],tuple(tasks),method['repeats'],method['seed'],method['max_trials'],method['min_roots'],
            method['effect_margin'],method['family_margin'],method['alpha'],tuple(sorted(method['family_weights'].items())),
            'declared-baseline','declared-candidate','declared-oracle','declared-runtime',profile='certification',partition='certification')
        return preflight(spec,trial_limit=method['max_trials'])

    def test_original_two_root_two_family_design_remains_analytically_infeasible(self):
        result=self.precision(ConditionalApplicationKernelTests.FINAL_METHOD,
            [('regression','calculator','regression'),('tool-recovery','recovery','tool-recovery')])
        self.assertEqual(result['status'],'infeasible')
        self.assertFalse(result['endpoints']['primary']['criteria_possible'])

    def test_new_ten_root_fixed_design_is_analytically_feasible_before_any_dispatch(self):
        result=self.precision(TEN_METHOD,[(name,name,'synthetic-artifact-transforms') for name,_,_,_ in TEN_CASES])
        self.assertEqual(result['status'],'feasible');self.assertEqual(result['planned_trials'],20)
        self.assertEqual(result['declared_roots'],10);self.assertTrue(result['execution_allowed'])
        self.assertTrue(all(value['criteria_possible'] for value in result['endpoints'].values() if value['required']))
        self.assertEqual(result['root_independence'],'unverified')

    def test_negative_workers_are_predeclared_without_changing_fixed_final_inputs(self):
        compile(NO_CHANGE_PROPOSER,'synthetic-no-change','exec')
        compile(INVALID_AUTHOR,'synthetic-invalid-author','exec')
        self.assertNotEqual(INVALID_AUTHOR,roles.AUTHOR)
        self.assertIn('valid=False,outcome=outcome',INVALID_AUTHOR)
        self.assertNotIn('AGENTS.md',NO_CHANGE_PROPOSER)
        self.assertEqual(TEN_METHOD['alpha'],.05);self.assertEqual(TEN_METHOD['max_trials'],20)

    def test_new_prospective_bank_has_ten_distinct_fixed_inputs_and_complete_controls(self):
        from agent_harness_lab.evaluation.task_bank import validate
        with tempfile.TemporaryDirectory() as folder:
            bank,tasks=ten_root_bank(Path(folder)/'new-bank');checked=validate(bank)
            self.assertEqual(checked['tasks'],10);self.assertEqual(checked['issues'],[])
            self.assertEqual(checked['content_grouping']['overlaps'],0)
            self.assertFalse(checked['certification_eligible'])
            for task in tasks:
                bundle=bank.parent/task['grader_bundle']
                controls=json.loads((bundle/'controls.json').read_bytes())
                self.assertEqual({control['state'] for control in controls},
                    {'broken','partial','complete','alternative_valid','superficial'})
                compile((bundle/'grade.py.txt').read_text(),str(bundle/'grade.py.txt'),'exec')


@unittest.skipUnless(kernel.ENABLED, 'explicit delegated native scope required')
class TenRootConditionalApplicationKernelTests(ConditionalApplicationKernelTests):
    FINAL_METHOD=TEN_METHOD
    TRIAL_LIMIT=32
    FINAL_TASKS={name for name,_,_,_ in TEN_CASES}
    FEASIBLE=True
    WORKER=TEN_EXECUTOR

    def reviewed_bank(self):
        bank,tasks=ten_root_bank(self.root/'ten-root-final-source')
        return self.qualify_bank(bank,tasks)

    def proposer_argv(self):
        if getattr(self,'negative',None)=='no_change':return ['/usr/bin/python3','-c',NO_CHANGE_PROPOSER]
        return super().proposer_argv()

    def author_argv(self):
        if getattr(self,'negative',None)=='invalid_grader':return ['/usr/bin/python3','-c',INVALID_AUTHOR,'1','complete']
        return super().author_argv()

    def executor_argv(self,request,context):
        # The fake model sees only the actual serialized request, including the task prompt.
        if getattr(self,'negative',None) in ('insufficient','failed_final') and any(
                prompt in request['input'][1]['content'] for _,prompt,_,_ in TEN_CASES):
            return ['/usr/bin/python3','-c',self.WORKER,'good' if self.negative=='insufficient' else 'bad']
        return super().executor_argv(request,context)

    def target(self):
        path=self.project/'AGENTS.md'
        return path.read_bytes(),path.stat().st_mode&0o777

    def invoke_negative(self,*,fault=None):
        output=io.StringIO();errors=io.StringIO()
        registries=(application._SESSIONS,application._FINAL_RUNS,application._GRANTS)
        counts=tuple(map(len,registries))
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ,{'XDG_STATE_HOME':str(self.root/'state')}))
            stack.enter_context(patch.object(responses,'read_credential',return_value=None))
            stack.enter_context(patch.object(responses,'request',side_effect=fault or self.transport))
            stack.enter_context(redirect_stdout(output));stack.enter_context(redirect_stderr(errors))
            try:code=main(['improve',str(self.project)])
            except SystemExit as exc:code=exc.code
        observed=self.target() # Read bytes/mode even when the public endpoint rejects the invocation.
        self.assertEqual(tuple(map(len,registries)),counts)
        results=list((self.root/'state').rglob('workflow.json'))
        public_result=json.loads(output.getvalue()) if output.getvalue().strip() else None
        persisted_result=json.loads(results[-1].read_bytes()) if results else None
        return observed,code,public_result,persisted_result,errors.getvalue()

    def assert_spent(self,expected_calls,*,state='application_not_applied',journal=None):
        self.assertEqual(len(self.calls),expected_calls)
        _,_,events=self.store.opened(self.authorization)
        self.assertEqual(events[-1][0]['state'],state)
        started=[event for event,_ in events if event['kind']=='application_started']
        self.assertEqual(len(started),0 if state=='application_authorized' else 1)
        receipts=list((self.root/'state').rglob('inference.json'))
        if not expected_calls:self.assertEqual(receipts,[])
        if expected_calls:
            self.assertEqual(len(receipts),1)
            self.assertEqual(json.loads(receipts[0].read_bytes())['request_attempts'],expected_calls)
        records=[json.loads(path.read_bytes()) for path in (self.root/'state'/'promotions').rglob('*.json') if path.name!='active.json']
        if journal is None:self.assertEqual(records,[])
        else:
            self.assertEqual(len(records),1);self.assertEqual(records[0]['status'],journal)
            self.assertFalse(list((self.root/'state'/'promotions').rglob('active.json')))
        before=len(self.calls);target=self.target()
        counts=tuple(map(len,(application._SESSIONS,application._FINAL_RUNS,application._GRANTS)))
        with patch.dict(os.environ,{'XDG_STATE_HOME':str(self.root/'state')}), \
                patch.object(responses,'read_credential',side_effect=AssertionError('replay credential read')), \
                patch.object(responses,'request',side_effect=AssertionError('replay transport')), \
                redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):main(['improve',str(self.project)])
        self.assertEqual(self.target(),target)
        self.assertEqual(tuple(map(len,(application._SESSIONS,application._FINAL_RUNS,application._GRANTS))),counts)
        self.assertEqual(len(self.calls),before)
        self.assertEqual(self.store.opened(self.authorization)[2][-1][1],events[-1][1])
        return records

    def reservations(self):
        path=self.root/'history'/'protected-access.json'
        return json.loads(path.read_bytes())['reservations'] if path.exists() else {}

    def assert_final_spent(self,*,outcome=None):
        entries=list(self.reservations().values());self.assertEqual(len(entries),1)
        self.assertEqual(entries[0]['state'],'retired')
        history=HistoryStore(self.root/'history');case=history.case(entries[0]['run_id'])
        self.assertEqual(history.history(case)[-1][0]['state'],'sealed')
        trials=json.loads((case/'execution/results.json').read_bytes())['trials']
        self.assertEqual(len(trials),20)
        if outcome is not None:self.assertEqual({trial['status'] for trial in trials},{outcome})

    def test_negative_no_change_consumes_intent_without_final_or_mutation(self):
        self.negative='no_change';self.setup_authorities();before=self.target()
        observed,code,result,_,_=self.invoke_negative()
        self.assertEqual(observed,before)
        self.assertEqual((code,result['status']),(0,'no_change'),result)
        self.assert_spent(2)
        self.assertEqual(self.reservations(),{})

    def test_negative_equal_final_outcomes_do_not_support_application(self):
        self.negative='insufficient';self.setup_authorities();before=self.target()
        observed,code,result,_,_=self.invoke_negative()
        self.assertEqual(observed,before)
        self.assertEqual((code,result['status'],result['reason']),(3,'inconclusive','conditional_final_not_supported'),result)
        self.assertNotEqual(result['analysis']['status'],'supported')
        self.assert_spent(56);self.assert_final_spent(outcome='passed')

    def test_negative_failed_final_outcomes_do_not_support_application(self):
        self.negative='failed_final';self.setup_authorities();before=self.target()
        observed,code,result,_,_=self.invoke_negative()
        self.assertEqual(observed,before)
        self.assertEqual((code,result['status'],result['reason']),(3,'inconclusive','conditional_final_not_supported'),result)
        self.assertNotEqual(result['analysis']['status'],'supported')
        self.assert_spent(56);self.assert_final_spent(outcome='failed')

    def test_negative_invalid_authored_grader_blocks_before_final(self):
        self.negative='invalid_grader';self.setup_authorities();before=self.target()
        observed,code,result,persisted,error=self.invoke_negative()
        self.assertEqual(observed,before)
        self.assertEqual(code,2);self.assertIsNone(result)
        self.assertEqual((persisted['status'],persisted['error_type']),('invalid_input','ValueError'),persisted)
        self.assertIn('Grader self-test rejected',error)
        self.assert_spent(4)
        self.assertEqual(self.reservations(),{})

    def test_negative_unavailable_runtime_does_not_consume_authorization(self):
        self.setup_authorities();before=self.target();self.runtime.rename(self.root/'unavailable-runtime')
        observed,code,result,persisted,error=self.invoke_negative()
        self.assertEqual(observed,before)
        self.assertEqual(code,2);self.assertIsNone(result);self.assertIsNone(persisted)
        self.assertIn('controlled Responses reference is unavailable',error)
        self.assert_spent(0,state='application_authorized')

    def test_negative_interrupted_transport_keeps_spent_request_and_blocks_replay(self):
        self.setup_authorities();before=self.target()
        def interrupted(*args):
            self.calls.append(json.loads(args[1]));raise KeyboardInterrupt
        observed,code,result,_,_=self.invoke_negative(fault=interrupted)
        self.assertEqual(observed,before)
        self.assertEqual((code,result['status']),(1,'interrupted'),result)
        self.assert_spent(1)
        receipt=json.loads(next((self.root/'state').rglob('inference.json')).read_bytes())
        self.assertTrue(receipt['request_pending']);self.assertTrue(receipt['further_requests_blocked'])

    def test_negative_signed_request_budget_exhausts_without_reset_or_replay(self):
        self.setup_authorities(max_requests=1);before=self.target()
        observed,code,result,_,_=self.invoke_negative()
        self.assertEqual(observed,before)
        self.assertEqual((code,result['status'],result['reason']),(1,'budget_exhausted','inference_request_count_exhausted'),result)
        self.assert_spent(1)

    def test_negative_partial_journal_write_interrupt_restores_owned_target(self):
        from agent_harness_lab.promotion.journal import Journal
        self.setup_authorities();before=self.target();write=Journal._write_snapshot;written=[]
        def interrupted(journal,*args,**kwargs):
            value=write(journal,*args,**kwargs)
            if not written:
                written.append(self.target());raise KeyboardInterrupt
            return value
        with patch.object(Journal,'_write_snapshot',interrupted):observed,code,result,_,_=self.invoke_negative()
        self.assertEqual(observed,before)
        self.assertEqual(written,[(b'good\nSYNTHETIC_CANDIDATE_SECRET\n',before[1])])
        self.assertEqual((code,result['status']),(1,'interrupted'),result)
        records=self.assert_spent(56,journal='conflict');self.assert_final_spent()
        self.assertEqual((records[0]['restoration'],records[0]['remaining_changes']),('restored',[]))

    def test_negative_foreign_target_conflict_preserves_foreign_bytes(self):
        from agent_harness_lab.promotion.journal import Journal
        self.setup_authorities();before=self.target();execute=Journal._execute
        def changed(journal,*args,**kwargs):
            (self.project/'AGENTS.md').write_text('synthetic foreign edit\n')
            return execute(journal,*args,**kwargs)
        with patch.object(Journal,'_execute',changed):observed,code,result,persisted,error=self.invoke_negative()
        self.assertEqual(observed,(b'synthetic foreign edit\n',before[1]))
        self.assertEqual(code,2);self.assertIsNone(result)
        self.assertEqual((persisted['status'],persisted['error_type']),('invalid_input','ValueError'),persisted)
        self.assertIn('intervening edit/mode/path conflict',error)
        self.assert_spent(56,journal='prepared');self.assert_final_spent()
        self.assertEqual(self.target(),observed)

    def test_negative_public_retirement_before_mutation_invalidates_grant(self):
        from agent_harness_lab.promotion.journal import Journal
        self.setup_authorities();before=self.target();execute=Journal._execute;retired=[]
        def revoke(journal,*args,**kwargs):
            with redirect_stdout(io.StringIO()):
                retired.append(main(['application-review','--store',str(self.store.root),'retire',self.authorization]))
            return execute(journal,*args,**kwargs)
        with patch.object(Journal,'_execute',revoke):observed,code,result,persisted,error=self.invoke_negative()
        self.assertEqual(observed,before)
        self.assertEqual(retired,[0])
        self.assertEqual(code,2);self.assertIsNone(result)
        self.assertEqual((persisted['status'],persisted['error_type']),('invalid_input','ValueError'),persisted)
        self.assertIn('application running authorization changed or was retired',error)
        records=self.assert_spent(56,state='retired',journal='conflict');self.assert_final_spent()
        self.assertEqual((records[0]['restoration'],records[0]['remaining_changes']),('restored',[]))


if __name__=='__main__':unittest.main()
