"""Actual synthetic offline comparisons; no native identity or model claims."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

import agent_harness_lab
from agent_harness_lab.cli import evaluator
from agent_harness_lab.evaluation import decision_receipts
from agent_harness_lab.evaluation.bank_tools import export_manifest
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.oracle_history import HistoryStore
from agent_harness_lab.evaluation.plan import ExperimentSpec
from agent_harness_lab.evaluation.sandbox import SandboxBackend
from agent_harness_lab.optimization.history_evidence import HistorySearchContext,feasibility,runtime_hash,scan
from agent_harness_lab.optimization.search import CandidateRecord
from agent_harness_lab.optimization.search import search
from agent_harness_lab.optimization.budget import Budget
from agent_harness_lab.private_journal import encoded,record
if __package__:
    from . import test_preservation_history as fixtures
    from . import test_preservation_analysis as math_fixtures
    from . import test_oracle_history as histories
    from . import test_regression_admission as admissions
else:
    import test_preservation_history as fixtures
    import test_preservation_analysis as math_fixtures
    import test_oracle_history as histories
    import test_regression_admission as admissions


def candidate(name,changes):
    return CandidateRecord(name,(),'synthetic bounded repair',changes,('synthetic:control',),
        'Compare the exact synthetic harness patch','Any missing endpoint blocks qualification')


def resources(fixture):
    return dict(cgroup_root=fixture.root/'delegation',image_root=fixture.root/'image',image_sha256=fixture.image_hash,
        disk_bytes=64*1024*1024,budget_seconds=180,work_root=fixture.root/'prepared-search')


class OptimizationHistoryTests(unittest.TestCase):
    def fixture(self):
        source=fixtures.PreservationHistoryTests(); source.setUp(); self.addCleanup(source.doCleanups)
        return source.f

    def context(self,fixture,allowed=('solve.py','nested/new.txt')):
        return HistorySearchContext(fixture.store.root,fixture.baseline,fixture.export/'manifest.json',resources(fixture),list(allowed))

    def evaluate(self,fixture,context,value,stage='development',event='synthetic:event',**options):
        with patch.object(SandboxBackend,'capability',return_value={'status':'available'}), \
                patch.object(SandboxBackend,'run',side_effect=fixture.execute) as workers:
            result=context.evaluate(value,stage=stage,event_id=event,**options)
        self.assertEqual(workers.call_count,8)
        return result

    def test_runtime_hash_matches_native_path_order_including_dotted_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/'a').mkdir(); (root/'a/item').write_text('nested'); (root/'a.txt').write_text('sibling')
            self.assertEqual(runtime_hash(scan(root)[1]),evaluator().digest(root))

    def test_actual_sparse_no_op_and_patch_runs_bind_modes_and_reverify_without_dispatch(self):
        fixture=self.fixture(); (fixture.baseline/'empty').mkdir(mode=0o751); (fixture.baseline/'solve.py').chmod(0o740)
        context=self.context(fixture); starts=[]
        baseline=self.evaluate(fixture,context,candidate('base',{}),'baseline','synthetic:baseline',on_started=starts.append)
        self.assertFalse(baseline['safe']); self.assertTrue(baseline['retain_no_op']); self.assertEqual(baseline['metrics'],{})
        self.assertEqual(starts,[baseline['binding']['run_id']])
        program=(fixture.baseline/'solve.py').read_text()+'\n# independently synthetic authorized variant\n'
        changed=self.evaluate(fixture,context,candidate('patched',{'solve.py':program,'nested/new.txt':'synthetic note'}))
        binding=changed['binding']; self.assertNotEqual(binding['patch_sha256'],binding['harness_sha256'])
        attempt=context.work/binding['template_sha256']/binding['attempt_id']
        self.assertEqual((attempt/'candidate/solve.py').stat().st_mode&0o777,0o740)
        self.assertEqual((attempt/'candidate/empty').stat().st_mode&0o777,0o751)
        self.assertEqual((attempt/'candidate/nested').stat().st_mode&0o777,0o700)
        self.assertEqual((attempt/'candidate/nested/new.txt').stat().st_mode&0o777,0o600)
        with patch.object(context.store,'run',side_effect=AssertionError('verification must not dispatch')):
            self.assertEqual(context.verify(binding),changed)
        with self.assertRaises(FileExistsError): context.evaluate(candidate('patched',{'solve.py':program,'nested/new.txt':'synthetic note'}),stage='development',event_id='synthetic:event')
        self.assertFalse(changed['automatic_promotion']); self.assertFalse(changed['adoption_authority'])

    def test_forged_patch_harness_event_template_and_run_refs_cannot_qualify(self):
        fixture=self.fixture(); context=self.context(fixture)
        result=self.evaluate(fixture,context,candidate('base',{}),'baseline'); original=result['binding']
        mutations=[lambda value:value.update(safe=True,metrics={'primary_effect':99}),
            lambda value:value.update(harness_sha256='f'*64),lambda value:value.update(template_sha256='f'*64),
            lambda value:value.update(event_id='another:event'),lambda value:value.update(run_id='f'*64),
            lambda value:value.update(stage='development_confirmation'),
            lambda value:value['candidate_record']['changes'].update({'solve.py':'print("forged")'})]
        for mutate in mutations:
            value=deepcopy(original); mutate(value)
            with self.subTest(value=value),self.assertRaises((ValueError,OSError)): context.verify(value)
        attempt=context.work/original['template_sha256']/original['attempt_id']
        (attempt/'candidate/solve.py').chmod(0o700)
        with self.assertRaisesRegex(ValueError,'harness'): context.verify(original)

    def test_live_authority_and_engine_changes_revoke_existing_bindings(self):
        fixture=self.fixture(); context=self.context(fixture)
        result=self.evaluate(fixture,context,candidate('base',{}),'baseline'); binding=result['binding']
        engine=decision_receipts.engine(); engine['python']['version']='synthetic-other-engine'
        with patch.object(decision_receipts,'engine',return_value=engine):
            with self.assertRaisesRegex(ValueError,'regenerate'): context.verify(binding)
        fixture.revoke()
        with self.assertRaises(ValueError): context.verify(binding)
        with self.assertRaises(ValueError): _=context.template_binding

    def test_interrupted_start_keeps_genuine_incomplete_identity_without_redispatch(self):
        fixture=self.fixture(); context=self.context(fixture); observed=[]
        def interrupt(run_id): observed.append(run_id); raise KeyboardInterrupt
        with patch.object(SandboxBackend,'run') as worker:
            with self.assertRaises(KeyboardInterrupt):
                context.evaluate(candidate('base',{}),stage='baseline',event_id='synthetic:interrupt',on_started=interrupt)
        worker.assert_not_called(); self.assertEqual(len(observed),1)
        self.assertEqual(context.store.show(observed[0])['state'],'incomplete')
        captured=list(context.work.rglob('started-run.json'))
        self.assertEqual(record(captured[0])[0],dict(run_id=observed[0]))
        with self.assertRaises(FileExistsError): context.evaluate(candidate('base',{}),stage='baseline',event_id='synthetic:interrupt')

    def test_preparation_deadline_overlap_and_frozen_source_fail_before_workers(self):
        fixture=self.fixture(); context=self.context(fixture)
        with patch.object(SandboxBackend,'run') as worker:
            with self.assertRaises(TimeoutError): context.evaluate(candidate('base',{}),stage='baseline',event_id='expired',parent_deadline=time.monotonic()-1)
        worker.assert_not_called(); self.assertFalse(context.work.exists())
        for work in (fixture.baseline,fixture.store.root,fixture.export):
            profile=resources(fixture)|dict(work_root=work)
            with self.subTest(work=work),self.assertRaises(ValueError):
                HistorySearchContext(fixture.store.root,fixture.baseline,fixture.export/'manifest.json',profile,['solve.py'])
        with self.assertRaises(ValueError):
            HistorySearchContext(fixture.store.root,fixture.baseline,fixture.export/'manifest.json',resources(fixture),['x'*4097])
        with self.assertRaises(ValueError):
            HistorySearchContext(fixture.store.root,fixture.baseline,fixture.export/'manifest.json',resources(fixture),['/'.join(['segment']*65)])
        with self.assertRaises(ValueError):
            context.evaluate(candidate('oversized',{'x'*4097:'body'}),stage='development',event_id='oversized')
        context.resources['budget_seconds']=300
        with self.assertRaises(ValueError): _=context.template_binding
        context.resources['budget_seconds']=180
        context.allowed.append('unapproved.txt')
        with self.assertRaises(ValueError): _=context.template_binding
        context.allowed.remove('unapproved.txt')
        (fixture.baseline/'solve.py').write_text('print("changed after freeze")')
        with self.assertRaises(ValueError): context.evaluate(candidate('base',{}),stage='baseline',event_id='changed')

    def test_conditional_power_supports_no_op_without_superiority_and_candidate_all_endpoints(self):
        spec,binding=math_fixtures.freeze(math_fixtures.plan())
        aa=math_fixtures.analyze(spec,math_fixtures.records(spec,lambda row:True),cohort_binding=binding)|dict(oracle_validity='current')
        self.assertEqual(aa['status'],'inconclusive')
        no_op=feasibility(spec,aa,baseline=True)
        self.assertTrue(no_op['qualified_feasible']); self.assertEqual(no_op['metrics'],{'primary_effect':0.0})
        positive=math_fixtures.analyze(spec,math_fixtures.records(spec,math_fixtures.successful_preservation),cohort_binding=binding)|dict(oracle_validity='current')
        supported=feasibility(spec,positive,baseline=False)
        self.assertTrue(supported['qualified_feasible']); self.assertGreater(supported['metrics']['primary_effect'],0)
        positive['preservation']['cohorts']['protected']['disposition']='inconclusive'
        self.assertFalse(feasibility(spec,positive,baseline=False)['safe'])
        aa['preservation']['descriptive']['coverage']['unknown_grades']=1
        self.assertFalse(feasibility(spec,aa,baseline=True)['safe'])

    def test_export_empty_directory_amplification_rejects_before_prepared_allocation(self):
        fixture=self.fixture(); profile=resources(fixture); work=Path(profile['work_root'])
        for task in fixture.manifest['tasks']:
            for index in range(2700): (fixture.export/task['fixture']/('synthetic-empty-'+str(index))).mkdir()
        original=shutil.copytree; prepared=[]
        def copy(source,destination,*args,**kwargs):
            if Path(destination).is_relative_to(work):
                prepared.append(str(destination)); raise AssertionError('prepared copy reached before aggregate admission')
            return original(source,destination,*args,**kwargs)
        with patch('agent_harness_lab.optimization.history_evidence.shutil.copytree',side_effect=copy):
            with self.assertRaisesRegex(ValueError,'entry'):
                context=HistorySearchContext(fixture.store.root,fixture.baseline,fixture.export/'manifest.json',profile,['solve.py'])
                context.evaluate(candidate('base',{}),stage='baseline',event_id='synthetic:entry-budget')
        self.assertEqual(prepared,[]); self.assertFalse(work.exists())

    def test_baseline_changed_during_last_numeric_verify_cannot_return_fresh(self):
        fixture=self.fixture(); context=self.context(fixture)
        binding=self.evaluate(fixture,context,candidate('base',{}),'baseline')['binding']
        original=context.store.verify_decision; count=[0]
        def mutate(*args,**kwargs):
            result=original(*args,**kwargs); count[0]+=1
            if count[0]==2:
                path=fixture.baseline/'solve.py'; path.write_text(path.read_text()+'\n# synthetic concurrent source mutation\n')
            return result
        with patch.object(context.store,'verify_decision',side_effect=mutate):
            with self.assertRaisesRegex(ValueError,'source or baseline'): context.verify(binding)
        self.assertEqual(count[0],2)


class OptimizationPositiveHistoryTests(unittest.TestCase):
    def test_actual_fresh_both_arms_can_qualify_under_explicit_synthetic_power_policy(self):
        owner=admissions.RegressionAdmissionTests(); owner.setUp(); self.addCleanup(owner.doCleanups)
        pack=Path(agent_harness_lab.__file__).parent/'evaluation/fixtures/adversarial-v1'
        prototype=record(pack/'bank.json',1024*1024)[0]['tasks'][0]
        bank=record(owner.parent)[0]; selected=[]; bank['tasks']=[]
        for index in range(12):
            name='synthetic-numeric-'+str(index); selected.append(name); task=deepcopy(prototype)
            task.update(task_id=name,root_id=name,family='synthetic-numeric',fixture='tasks/'+name,
                grader_bundle='graders/'+name,source_ref='synthetic:independent-control:'+str(index))
            fixture=owner.parent.parent/task['fixture']; oracle=owner.parent.parent/task['grader_bundle']
            fixture.parent.mkdir(exist_ok=True); oracle.parent.mkdir(exist_ok=True)
            shutil.copytree(pack/prototype['fixture'],fixture); shutil.copytree(pack/prototype['grader_bundle'],oracle)
            body=record(fixture/'task.json')[0]
            body['inputs']=dict(operation='sum',values=[index,3],case=index)
            body['prompt']='Sum the synthetic values ['+str(index)+', 3]; preserve protected.txt and leave sink.txt empty.'
            (fixture/'task.json').write_bytes(encoded(body))
            expected=record(oracle/'expected.json')[0]; expected['result']['value']=index+3
            (oracle/'expected.json').write_bytes(encoded(expected))
            policy=record(oracle/'behavior.json')[0]; policy.update(task_id=name,root_id=name,family='synthetic-numeric')
            (oracle/'behavior.json').write_bytes(encoded(policy))
            for control in json.loads((oracle/'controls.json').read_text()):
                result=oracle/control['fixture']/'result.json'
                result.write_bytes(encoded(dict(value=index+3 if control['state'] in ('complete','alternative_valid','superficial') else index+4)))
            task.update(fixture_digest=digest(fixture),grader_digest=digest(oracle)); bank['tasks'].append(task)
        owner.parent.write_bytes(encoded(bank)); accepted=owner.accept(); bank_path=owner.bank(accepted)
        fixture=histories.OracleHistoryTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        fixture.export=fixture.root/'export'; shutil.rmtree(fixture.export)
        fixture.manifest=export_manifest(bank_path,fixture.export,max_trials=40,task_ids=selected)
        for task in fixture.manifest['tasks']: task['offline_executor']=['/usr/bin/python3','-I','/inputs/harness/solve.py']
        fixture.all_bundles=deepcopy(fixture.manifest['grader_bundles'])
        program="""import json
from pathlib import Path
inputs=json.loads(Path('task.json').read_text())['inputs']
answer=sum(inputs['values']) if inputs['case']<4 else -999
Path('result.json').write_text(json.dumps(dict(value=answer)))
"""
        fixture.baseline=fixture.args.baseline; fixture.candidate=fixture.args.candidate
        (fixture.baseline/'solve.py').write_text(program)
        (fixture.candidate/'solve.py').write_text(program.replace("inputs['case']<4",'True'))
        fixture.install_plan(fixture.manifest['tasks'])
        prior,calls=fixture.run_history(); self.assertEqual((prior['state'],calls),('sealed',48))
        prior_plan=fixture.store.historical_plan(prior['run_id']); inputs=record(fixture.store.case(prior['run_id'])/'inputs.json')[0]
        originals={task['task_id']:task for task in record(bank_path,1024*1024)[0]['tasks']}
        members=[]
        for name in selected[:4]:
            task=originals[name]; bound=inputs['bank_tasks'][name]
            members.append(dict(task_id=name,root_id=task['root_id'],family=task['family'],
                bank_sha256=inputs['bank_authority']['bank_sha256'],bank_revision_sha256=inputs['bank_authority']['revision_sha256'],
                task_revision_sha256=bound['task_revision_sha256'],fixture_sha256=task['fixture_digest'],
                materialized_sha256=bound['materialized_sha256'],execution_fixture_sha256=bound['execution_fixture_sha256'],
                bundle_sha256=task['grader_digest'],grader_version=task['grader_version']))
        qualification=dict(history_store=str(fixture.store.root),run_id=prior['run_id'],event_sha256=prior_plan['event_sha256'],
            evidence_sha256=prior_plan['evidence_sha256'],plan_sha256=prior_plan['plan_sha256'],arm='baseline',
            rule=dict(min_trials=1,min_successes=1),review=dict(identity='synthetic-reviewer',source='operator_attestation',
                basis_kind='independent_artifact_review',evidence_refs=[dict(ref='synthetic:review',sha256='a'*64)]))
        # Explicit permissive test policy exercises the positive path cheaply; production defaults stay unchanged.
        cohort=dict(schema_version=1,profile='preservation-cohorts-v1',author='synthetic-author',cohorts=[dict(
            cohort_id='preserved',role='historical_pass',required=True,margin=.99,exposure='development_regression',
            severity_policy='verified_critical_veto',qualification=qualification,members=members)])
        raw=encoded(cohort); (fixture.export/'cohorts.json').write_bytes(raw); pin=hashlib.sha256(raw).hexdigest()
        fixture.manifest['cohort_manifest']=dict(path='cohorts.json',sha256=pin)
        spec=replace(ExperimentSpec.parse(fixture.manifest['experiment']),cohort_manifest_sha256=pin,alpha=.9,family_margin=.99)
        fixture.manifest['experiment']=spec.to_dict(); fixture.write_manifest()
        context=HistorySearchContext(fixture.store.root,fixture.baseline,fixture.export/'manifest.json',resources(fixture),['solve.py'])
        patch_value=candidate('repair',{'solve.py':(fixture.candidate/'solve.py').read_text()})
        def forbidden(*args): self.fail('raw evaluate/certify callbacks cannot authorize guarded search')
        with patch.object(SandboxBackend,'capability',return_value={'status':'available'}), \
                patch.object(SandboxBackend,'run',side_effect=fixture.execute) as workers:
            result=search(fixture.root/'search-state/search.json',candidate('base',{}),lambda index:patch_value,
                forbidden,forbidden,Budget(6,1),['solve.py'],enabled=True,variants=1,controller=context,
                parent_deadline=time.monotonic()+600)
        self.assertEqual(workers.call_count,144)
        self.assertEqual(result['status'],'development_recommended_for_review',result.get('reason'))
        self.assertTrue(all(row['safe'] for row in result['candidates']))
        self.assertEqual(result['candidates'][0]['metrics'],{'primary_effect':0.0})
        self.assertAlmostEqual(result['candidates'][1]['metrics']['primary_effect'],8/12)
        bindings=[row['binding'] for row in result['attempts']]+[result['confirmation']['binding']]
        self.assertEqual(len({binding['run_id'] for binding in bindings}),3)
        trials=[{row['trial_id'] for row in record(context.store.case(binding['run_id'])/'execution/results.json',8*1024*1024)[0]['trials']} for binding in bindings]
        self.assertTrue(trials[1].isdisjoint(trials[2]))
        self.assertFalse(result['adoption_authority']); self.assertFalse(result['automatic_promotion'])
        self.assertFalse(result['publication_allowed'])


if __name__=='__main__': unittest.main()
