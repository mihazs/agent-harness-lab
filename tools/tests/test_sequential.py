"""Published conditional WoR math and fresh synthetic execution; no real models."""
from copy import copy, deepcopy
from dataclasses import replace
from fractions import Fraction
from decimal import Decimal,localcontext,getcontext
import itertools
import hashlib
import json
import math
import os
import random
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import agent_harness_lab
from agent_harness_lab.evaluation import sequential as core, sequential_state as custody, responses, benchmark
from agent_harness_lab.evaluation.plan import ExperimentSpec
from agent_harness_lab.evaluation.statistics import TrialRecord,analyze as fixed_analyze
from agent_harness_lab.evaluation.precision import preflight
from agent_harness_lab.evaluation.oracle import identity
from agent_harness_lab.evaluation.oracle_history import HistoryStore
from agent_harness_lab.evaluation.sandbox import SandboxBackend
if __package__:
    from . import test_offline_evaluation as offline
    from . import test_sandbox_inference as inference
    from . import test_preservation_analysis as preservation
    from . import test_preservation_history as qualified
else:
    import test_offline_evaluation as offline
    import test_sandbox_inference as inference
    import test_preservation_analysis as preservation
    import test_preservation_history as qualified


def spec(n=16,**kwargs):
    return ExperimentSpec('synthetic-sequential',tuple((f't{i}',f'r{i}','family') for i in range(n)),1,13,
        2*n,2,.05,.1,.05,(('family',1),),'b'*64,'c'*64,'g'*64,'i'*64,
        sequential=core.config(n,3,.25),**kwargs)


def records(plan,binding,outcome):
    return [TrialRecord(r['trial_id'],outcome(r),'graded','executable_grader',
        plan.baseline_hash,plan.candidate_hash,plan.grader_hash,plan.runtime_hash) for r in core.schedule(plan,binding)]


class SequentialMathTests(unittest.TestCase):
    def test_selected_import_root(self):
        expected=os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
        if expected:self.assertTrue(Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve()))

    def test_uniform_fisher_yates_is_not_a_user_seeded_shuffle(self):
        plan=spec(3);orders=set()
        for first in range(3):
            for second in range(2):
                with patch.object(core.secrets,'randbelow',side_effect=[first,second]) as draw:
                    binding=core.create_binding(plan,[])
                self.assertEqual([c.args[0] for c in draw.call_args_list],[3,2]);orders.add(tuple(binding['root_order']))
        self.assertEqual(len(orders),6)

    def test_preregistered_small_null_simulation_and_correlated_repeats(self):
        # Fixed before production checks: 256 SRS permutations, N32, alpha.05;
        # acceptable 99% binomial upper error <=.06. No automatic adoption tested here.
        plan=spec(32);binding=core.create_binding(plan,[]);rng=random.Random(2026110209);wrong=0
        for _ in range(256):
            vector=[Fraction(1)]*16+[Fraction(-1)]*16;rng.shuffle(vector)
            for n in range(2,33):
                result=core.endpoint(vector[:n],32,Fraction(1,20),binding['lambdas'])
                if result is None:break
                if result[0]>0:wrong+=1;break
        # Exact binomial CDF at .06: if this is <=.01, its one-sided99% upper
        # confidence endpoint is <=.06 (avoids approximate normal error bars).
        probability=sum(math.comb(256,k)*.06**k*.94**(256-k) for k in range(wrong+1))
        self.assertLessEqual(probability,.01,wrong)
        one=core.analyze(plan,records(plan,binding,lambda row:row['version']=='candidate'),binding)
        repeated=replace(plan,repeats=20,max_trials=1280,sequential=core.config(32,100,1))
        other=core.create_binding(repeated,[]);other['root_order']=binding['root_order']
        many=core.analyze(repeated,records(repeated,other,lambda row:row['version']=='candidate'),other)
        self.assertEqual(one['endpoints'],many['endpoints']);self.assertEqual(many['roots'],32)

    def test_absent_mode_preserves_exact_legacy_plan_and_schedule_bytes(self):
        from agent_harness_lab.evaluation.plan import schedule
        from agent_harness_lab.private_journal import encoded
        plan=replace(spec(),sequential=None)
        self.assertNotIn('sequential',plan.to_dict())
        self.assertEqual(plan.digest,'27bb105305f8a9643a0ac5966399d7f20f55bcf6da7d1c2011aec02937bb3bff')
        self.assertEqual(hashlib.sha256(encoded(schedule(plan))).hexdigest(),'ebbac77c4f7ae92ee840edf22234f924e54584ed97d9a626a7d77d6159a0c398')

    def test_logarithm_enclosure_is_outward_and_context_local(self):
        before=getcontext().copy()
        for alpha in (Fraction(1,20),Fraction(1,20640),Fraction(1,10**300)):
            upper=core.log_upper(alpha)
            with localcontext() as context:
                context.prec=100
                reference=(Decimal(2*alpha.denominator)/Decimal(alpha.numerator)).ln()
                self.assertGreater(Decimal(upper.numerator)/Decimal(upper.denominator),reference)
        self.assertEqual(getcontext().prec,before.prec)

    def test_reference_formula_and_exact_census(self):
        plan=spec();binding=core.create_binding(plan,[])
        for alpha in (Fraction(1,20),Fraction(1,60)):
            values=[Fraction(-1),Fraction(0),Fraction(1),Fraction(1)]
            result=core.endpoint(values,4,alpha,binding['lambdas'])
            self.assertEqual(result[:2],(Fraction(1,4),Fraction(1,4)))
            # Direct equation3.2–3.7 calculation independent of core.endpoint.
            s=a=b=q=0.
            for i,x in enumerate(values[:2],1):
                lam=float(core.unpack(binding['lambdas'][i-1]));a+=lam*(float(x)+s/(4-i+1));b+=lam*4/(4-i+1);q+=lam*lam/2;s+=float(x)
            low=max(-.75,a/b-(q+math.log(2/float(alpha)))/b)
            hi=min(.25,a/b+(q+math.log(2/float(alpha)))/b)
            actual=core.endpoint(values[:2],4,alpha,binding['lambdas'])
            self.assertAlmostEqual(float(actual[0]),low,places=12);self.assertAlmostEqual(float(actual[1]),hi,places=12)

    def test_all_six_exact_margin_orders_and_weighted_primary(self):
        plan=replace(spec(4),tasks=(('a','x','family'),('b','y','family'),('c','z','family'),('d','z','family')),
            repeats=10,max_trials=80,sequential=core.config(3,100,1))
        binding=core.create_binding(plan,[])
        def outcome(row):
            if row['task'] in ('a','b'):return row['version']=='candidate' and row['repetition']==0
            return row['task']=='c' and row['version']=='baseline' and row['repetition']==0
        for order in itertools.permutations(('x','y','z')):
            binding['root_order']=list(order);result=core.analyze(plan,records(plan,binding,outcome),binding)
            self.assertEqual(result['primary']['exact_low'],[1,20]);self.assertNotEqual(result['status'],'supported')
        # Exact normalized decimal weights and NI equality have no float upgrade.
        plan=replace(spec(4),tasks=(('a','x','one'),('b','y','one'),('c','z','two'),('d','w','two')),
            family_weights=(('one',.1),('two',.2)),alpha=.99)
        # Existing plan requires normalized weights; choose exact decimal thirds with sum1.
        plan=replace(plan,family_weights=(('one',.4),('two',.6)))
        binding=core.create_binding(plan,[])
        result=core.analyze(plan,records(plan,binding,lambda row:row['family']=='one' and row['version']=='candidate'),binding)
        self.assertEqual(result['primary']['exact_low'],[2,5]);self.assertEqual(result['family_weights']['two'],[3,5])

    def test_ni_equality_and_cohort_member_task_mean_differs_from_whole_root(self):
        plan=replace(spec(4),repeats=10,max_trials=80,sequential=core.config(4,100,1),family_margin=.1)
        binding=core.create_binding(plan,[])
        data=records(plan,binding,lambda row:row['version']=='baseline' and row['repetition']==0)
        result=core.analyze(plan,data,binding)
        self.assertEqual(result['endpoints']['family:family']['exact_low'],[-1,10])
        self.assertEqual(result['endpoints']['family:family']['disposition'],'supported')
        self.assertNotEqual(result['status'],'supported') # NI alone is not practical superiority.
        plan=replace(spec(4),tasks=(('t0','r0','family'),('t1','r0','family'),('t2','r1','family'),('t3','r1','family')),
            sequential=core.config(2,10,1))
        plan,frozen=preservation.freeze(replace(plan,cohort_manifest_sha256='d'*64),members=['t0','t2'])
        binding=core.create_binding(plan,core.membership(plan,frozen['manifest_json']))
        data=records(plan,binding,lambda row:row['version']==('baseline' if row['task'] in ('t0','t2') else 'candidate'))
        result=core.analyze(plan,data,binding,cohort_json=frozen['manifest_json'])
        self.assertEqual(result['primary']['exact_low'],[0,1])
        self.assertEqual(result['endpoints']['cohort:protected']['exact_high'],[-1,1])

    def test_sampling_budget_does_not_expand_population_or_repeats(self):
        plan=replace(spec(100),max_trials=4,sequential=core.config(2,3,.25))
        plan.validate();binding=core.create_binding(plan,[])
        self.assertEqual(len(core.schedule(plan,binding)),4)
        result=core.analyze(plan,records(plan,binding,lambda row:row['version']=='candidate'),binding)
        self.assertEqual(result['endpoints']['family:family']['population_roots'],100)
        self.assertEqual(result['unobserved_roots'],98)

    def test_sparse_exact_family_harm_precedes_favorable_minimum(self):
        plan=replace(spec(3),tasks=(('a','x','common'),('b','y','common'),('c','z','rare')),
            family_weights=(('common',.8),('rare',.2)))
        binding=core.create_binding(plan,[])
        data=records(plan,binding,lambda row:row['version']==('baseline' if row['family']=='rare' else 'candidate'))
        result=core.analyze(plan,data,binding)
        self.assertEqual(result['status'],'rejected')
        self.assertEqual(result['endpoints']['family:rare']['exact_high'],[-1,1])
        self.assertEqual(result['endpoints']['family:rare']['disposition'],'harm')
        data=records(plan,binding,lambda row:row['family']=='common' and row['version']=='candidate')
        result=core.analyze(plan,data,binding)
        self.assertEqual(result['status'],'inconclusive')
        self.assertEqual(result['endpoints']['family:rare']['disposition'],'insufficient_roots')

    def test_sparse_required_cohort_exact_harm_precedes_minimum(self):
        plan,frozen=preservation.freeze(replace(spec(3),cohort_manifest_sha256='d'*64),members=['t0'])
        binding=core.create_binding(plan,core.membership(plan,frozen['manifest_json']))
        data=records(plan,binding,lambda row:row['version']==('baseline' if row['task']=='t0' else 'candidate'))
        result=core.analyze(plan,data,binding,cohort_json=frozen['manifest_json'])
        self.assertEqual(result['status'],'rejected')
        self.assertEqual(result['endpoints']['cohort:protected']['exact_high'],[-1,1])
        self.assertEqual(result['endpoints']['cohort:protected']['disposition'],'harm')

    def test_subset_cohort_means_and_preservation_veto(self):
        base=replace(preservation.plan(100),sequential=core.config(100,3,.25))
        plan,frozen=preservation.freeze(base,members=[t[0] for t in base.tasks[:20]])
        binding=core.create_binding(plan,core.membership(plan,frozen['manifest_json']))
        data=records(plan,binding,lambda row:(int(row['task'][1:])<20)==(row['version']=='baseline'))
        result=core.analyze(plan,data,binding,cohort_json=frozen['manifest_json'])
        self.assertEqual(result['primary']['exact_low'],[3,5]);self.assertEqual(result['status'],'rejected')
        self.assertEqual(result['endpoints']['cohort:protected']['exact_high'],[-1,1])
        self.assertEqual(result['multiplicity']['endpoints'],2)

    def test_binding_plan_bounds_prefix_duplicates_and_fixed_consumers(self):
        plan=spec();binding=core.create_binding(plan,[]);data=records(plan,binding,lambda row:row['version']=='candidate')
        self.assertEqual(core.analyze(plan,data[:1],binding)['status'],'incomplete')
        self.assertEqual(core.analyze(plan,[data[0],data[0]],binding)['status'],'incomplete')
        self.assertEqual(fixed_analyze(plan,data)['reason'],'owned_sequential_inspection_required')
        with self.assertRaises(ValueError):preflight(plan)
        for changes in (dict(precision_half_width=.3),dict(profile='certification',partition='certification'),
                dict(sequential={**plan.sequential,'max_roots':True}),dict(sequential={**plan.sequential,'arm_seconds':float('nan')})):
            with self.subTest(changes=changes),self.assertRaises(ValueError):replace(plan,**changes).validate()
        original=core.schedule(plan,binding)
        alternate=deepcopy(binding);alternate['root_order'].reverse()
        first={(r['root'],r['task'],r['repetition']):[] for r in original}
        second=deepcopy(first)
        for row in original:first[row['root'],row['task'],row['repetition']].append(row['version'])
        for row in core.schedule(plan,alternate):second[row['root'],row['task'],row['repetition']].append(row['version'])
        self.assertEqual(first,second)
        for forged in ({},dict(binding,nonce='x'),dict(binding,root_order=[binding['root_order'][0]]*16)):
            with self.assertRaises((ValueError,TypeError)):core.validate_binding(plan,forged)

    def test_empty_intersection_never_restarts_and_shared_shock_is_not_expected_quality(self):
        # An extreme but possible alpha-error path can empty a running intersection.
        tuning=[[1,2]]*100
        self.assertIsNone(core.endpoint([Fraction(1)]*50+[Fraction(-1)]*50,100,Fraction(99,100),tuning))
        plan=spec();binding=core.create_binding(plan,[])
        for shock in (True,False):
            result=core.analyze(plan,records(plan,binding,lambda row:(row['version']=='candidate')==shock),binding)
            self.assertEqual(result['status'],'supported' if shock else 'rejected')
            self.assertEqual(result['future_expected_quality'],'unknown')
            self.assertFalse(result['automatic_promotion'])


class SequentialOnlineTests(unittest.TestCase):
    def setUp(self):
        self.f=offline.OfflineFixture();self.f.setUp();self.addCleanup(self.f.doCleanups)
        all_tasks=self.f.manifest['tasks'];seen=set();tasks=[]
        for task in all_tasks:
            if task['root'] not in seen:tasks.append(task);seen.add(task['root'])
        tasks=tasks[:3]
        for task in tasks:task['family']='family'
        self.f.manifest['tasks']=tasks
        self.f.manifest['grader_bundles']={t['id']:self.f.manifest['grader_bundles'][t['id']] for t in tasks}
        self.plan=ExperimentSpec('synthetic-fresh-online',tuple((t['id'],t['root'],t['family']) for t in tasks),1,13,6,2,
            .05,.1,.05,(('family',1),),self.f.runner.digest(self.f.root/'baseline'),self.f.runner.digest(self.f.root/'candidate'),
            identity(self.f.root/'export',tasks,self.f.manifest['grader_bundles']),'a'*64,sequential=core.config(3,3,.25))
        self.install()
        self.store=HistoryStore(self.f.root/'history');self.calls=[]

    def install(self):self.f.manifest['experiment']=self.plan.to_dict();self.f.write_manifest()

    def execute(self,*args,**kwargs):
        self.calls.append(args[3]);run=self.f.emulate(*args)
        run['attestation']=dict(backend='bubblewrap-offline-v1',role=args[3],network='none',aggregate_disk_bound_verified=True,
            cgroup_v2={'empty_after_cleanup':True},runtime_image=dict(sha256='a'*64,verified_before=True,verified_after=True))
        return run

    def run_history(self,execute=None,budget=30):
        with patch.object(SandboxBackend,'capability',return_value={'status':'available'}),patch.object(SandboxBackend,'run',side_effect=execute or self.execute):
            return self.store.run(self.f.args.manifest,self.f.args.baseline,self.f.args.candidate,cgroup_root=self.f.root/'delegation',
                image_root=self.f.root/'image',image_sha256='a'*64,disk_bytes=65536,budget_seconds=budget)

    def test_actual_fresh_both_arms_stop_early_and_guarded_receipt_replays_without_calls(self):
        result=self.run_history();self.assertEqual(result['state'],'sealed',result)
        self.assertEqual(self.calls.count('executor'),4);self.assertEqual(self.calls.count('grader'),4)
        analysis=self.store.analyze(result['run_id']);self.assertEqual(analysis['status'],'supported')
        self.assertEqual(analysis['completed_roots'],2);self.assertEqual(analysis['unobserved_roots'],1)
        receipt=self.store.decision(result['run_id']);self.assertFalse(receipt['adoption_authority'])
        with patch.object(SandboxBackend,'run',side_effect=AssertionError('must not resume or rerun')):
            self.assertEqual(self.store.verify_decision(result['run_id'],receipt['receipt_sha256']),receipt)
        case=self.store.case(result['run_id']);rows=json.loads((case/'execution/results.json').read_text())['trials']
        self.assertEqual({t['version'] for t in rows},{'baseline','candidate'})
        with self.assertRaises(ValueError):self.store.historical_plan(result['run_id'])
        # Reusing the same hypothesis never reuses rows or controller sampling custody.
        again=self.run_history();self.assertNotEqual(again['run_id'],result['run_id'])
        other=json.loads((self.store.case(again['run_id'])/'execution/results.json').read_text())['trials']
        self.assertTrue({r['trial_id'] for r in rows}.isdisjoint(r['trial_id'] for r in other))

    def test_caller_ceiling_admits_only_frozen_sequential_root_budget(self):
        self.plan=replace(self.plan,max_trials=4,sequential=core.config(2,3,.25))
        self.f.manifest['max_trials']=4;self.install()
        result=self.run_history();self.assertEqual(result['state'],'sealed',result)
        self.assertEqual(self.calls.count('executor'),4);self.assertEqual(self.calls.count('grader'),4)
        self.assertEqual(self.store.analyze(result['run_id'])['completed_roots'],2)
        self.plan=replace(self.plan,max_trials=6,sequential=None);self.install()
        with (patch.object(SandboxBackend,'capability',side_effect=AssertionError('no fixed-mode probe')),
                self.assertRaisesRegex(ValueError,'trial limit exceeded')):
            self.f.runner.evaluate(self.f.args)

    def test_harm_insufficient_and_no_root_budget_have_truthful_dispositions(self):
        first=self.f.root/'baseline/solve.py';second=self.f.root/'candidate/solve.py'
        a,b=first.read_bytes(),second.read_bytes();first.write_bytes(b);second.write_bytes(a)
        self.plan=replace(self.plan,baseline_hash=self.f.runner.digest(first.parent),candidate_hash=self.f.runner.digest(second.parent));self.install()
        result=self.run_history();self.assertEqual(result['state'],'sealed',result)
        self.assertEqual(self.store.analyze(result['run_id'])['status'],'rejected')
        self.assertEqual(self.calls.count('executor'),4)
        self.calls.clear();self.store=HistoryStore(self.f.root/'budget-history')
        self.plan=replace(self.plan,sequential=core.config(3,100,.25));self.install()
        result=self.run_history(budget=10);self.assertEqual(result['state'],'sealed',result)
        self.assertEqual(self.calls,[]);self.assertEqual(self.store.analyze(result['run_id'])['reason'],'root_budget_not_reserved')

    def test_missing_arm_and_interruption_are_incomplete_not_resumed(self):
        def missing(*args,**kwargs):
            if len(self.calls)==2:raise OSError('synthetic second arm unavailable')
            return self.execute(*args,**kwargs)
        result=self.run_history(missing);self.assertEqual(result['state'],'incomplete')
        self.assertNotEqual(self.store.analyze(result['run_id'])['status'],'supported')
        self.assertLessEqual(self.calls.count('executor'),1)

    def test_real_interrupt_retains_attempt_and_cold_restart_never_executes_it(self):
        def interrupt(*args,**kwargs):
            if args[3]=='executor':raise KeyboardInterrupt()
            return self.execute(*args,**kwargs)
        with self.assertRaises(KeyboardInterrupt):self.run_history(interrupt)
        cases=[p for p in (self.store.root/'cases').iterdir() if (p/'inputs.json').exists()]
        self.assertEqual(len(cases),1);case=cases[0]
        self.assertEqual(self.store.show(case.name)['state'],'incomplete')
        self.assertTrue((case/'sequential/0000-started.json').exists())
        self.assertFalse((case/'sequential/0000-inspected.json').exists())
        with self.assertRaises(FileExistsError):custody.Controller(self.plan,case/'sequential',time.monotonic()+30,inputs_sha256='0'*64)
        self.assertEqual(self.calls,[])

    def test_revocation_blocks_current_decision_without_new_execution(self):
        result=self.run_history();receipt=self.store.decision(result['run_id'])
        bundle=result['dependencies'][0]
        path=self.f.root/'quarantine.json'
        path.write_text(json.dumps(dict(schema_version=1,bundle_sha256=bundle,
            expected_event_sha256=self.store.oracle(bundle)['event_sha256'],
            operator=dict(identity='synthetic-reviewer',source='operator_attestation',provenance='public synthetic',basis='reference defect'),
            evidence_refs=[dict(ref='synthetic:defect',sha256='d'*64)],reason='synthetic oracle invalidation')))
        self.store.quarantine(path)
        with self.assertRaises(ValueError):self.store.verify_decision(result['run_id'],receipt['receipt_sha256'])
        self.assertEqual(self.store.analyze(result['run_id'])['status'],'blocked')
        with patch.object(SandboxBackend,'run',side_effect=AssertionError('no quarantined dispatch')),self.assertRaises(ValueError):self.run_history()

    def test_changed_sampling_or_inspection_seal_fails_closed(self):
        result=self.run_history();case=self.store.case(result['run_id'])
        binding=case/'sequential/binding.json';raw=json.loads(binding.read_text());raw['root_order'].reverse();binding.write_text(json.dumps(raw))
        with self.assertRaises(ValueError):self.store.show(result['run_id'])

    def test_frozen_remaining_quota_not_reduced_and_insufficient_family_stays_inconclusive(self):
        tasks=self.f.manifest['tasks']
        for i,task in enumerate(tasks):task['family']='rare' if i==0 else 'common'
        self.plan=replace(self.plan,tasks=tuple((t['id'],t['root'],t['family']) for t in tasks),
            family_weights=(('rare',.5),('common',.5)),sequential=core.config(2,3,.25),max_trials=4);self.install()
        limits=[];grade_budgets=[]
        from agent_harness_lab.evaluation import bank_tools
        original=bank_tools.regrade_artifacts
        def execute(*args,**kwargs):
            limits.append((args[3],args[4].seconds));return self.execute(*args,**kwargs)
        def grade(*args,**kwargs):
            grade_budgets.append(kwargs['limits'].seconds);return original(*args,**kwargs)
        with patch.object(bank_tools,'regrade_artifacts',side_effect=grade):result=self.run_history(execute)
        self.assertEqual(result['state'],'sealed',result)
        analysis=self.store.analyze(result['run_id']);self.assertEqual(analysis['status'],'inconclusive')
        self.assertEqual(analysis['reason'],'maximum_roots');self.assertEqual(len(limits),8)
        self.assertEqual({seconds for role,seconds in limits if role=='executor'},{.25})
        self.assertEqual(grade_budgets,[.25]*4)
        self.assertTrue(all(0<seconds<=.25 for _,seconds in limits))

    def test_engine_change_and_expired_direct_parent_cannot_continue(self):
        result=self.run_history();run_id=result['run_id']
        with patch.object(core,'engine_pin',return_value='f'*64),self.assertRaises(ValueError):self.store.analyze(run_id)
        # The public runner preserves the legacy already-exhausted status before sampling.
        self.f.args.parent_deadline=-1
        with patch.object(SandboxBackend,'capability',side_effect=AssertionError('no probe')),patch.object(SandboxBackend,'run',side_effect=AssertionError('no worker')):
            self.assertEqual(self.f.runner.evaluate(self.f.args),1)
        self.assertEqual(self.f.results()['reason'],'preflight_budget_exhausted')
        self.assertFalse((self.f.root/'output/sequential').exists())

    def test_live_sampling_and_prefix_mutation_are_detected_before_next_worker(self):
        seen=[]
        original=custody.Controller.before
        def before(control,scheduled,rows):
            if len(rows)==2 and not seen:
                seen.append(True);path=control.path/'0000-inspected.json'
                # Complete root is not inspected until this boundary; first inspect then mutate.
                control._inspect(rows);path.write_text('{}')
            return original(control,scheduled,rows)
        with patch.object(custody.Controller,'before',before):result=self.run_history()
        self.assertEqual(result['state'],'incomplete');self.assertLessEqual(self.calls.count('executor'),2)

    def test_critical_partial_veto_and_no_from_json_controller(self):
        plan=self.plan
        for seed in range(100):
            chosen=replace(plan,seed=seed)
            if core.schedule(chosen)[0]['version']=='candidate':plan=chosen;break
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(core.secrets,'randbelow',side_effect=lambda n:n-1):
                control=custody.Controller(plan,Path(folder)/'ledger',time.monotonic()+30,inputs_sha256='0'*64)
            schedule=control.schedule
            first=next(i for i,r in enumerate(schedule) if r['version']=='candidate')
            data=[]
            for row in schedule[:first+1]:
                data.append(dict(row,status='failed',grader=dict(outcome=False),
                    controller_grade=dict(outcome=False,valid=True,critical_violation=row['version']=='candidate')))
            self.assertTrue(control.before(schedule[0],[]))
            # The controller observes actual candidate critical evidence before a full root.
            control._inspect(data);result=control.finish(data)
            self.assertEqual(result['status'],'rejected');self.assertEqual(result['reason'],'critical_regression')
            self.assertEqual(first,0);self.assertEqual(result['completed_roots'],0)
            with self.assertRaises(ValueError):control.validate(plan)
            with self.assertRaises(FileExistsError):custody.Controller(plan,control.path,time.monotonic()+30,inputs_sha256='0'*64)
            with self.assertRaises(AttributeError):custody.Controller.from_json(control.binding)

    def test_private_labels_cannot_publish_or_authenticate_a_saved_result(self):
        result=self.run_history();case=self.store.case(result['run_id']);raw=json.loads((case/'execution/results.json').read_text())
        raw.update(publication_allowed=True,provenance=dict(source_kind='synthetic',source_ref='synthetic:sequential',
            approved_publication=True,derived_from_private=False))
        self.assertFalse(benchmark.public_export_allowed([raw]))
        self.assertFalse(benchmark.public_export_allowed([{**raw['sequential_analysis'],'publication_allowed':True,'provenance':raw['provenance']}]))
        raw['trials'][0]['grader']['outcome']=not raw['trials'][0]['grader']['outcome']
        (case/'execution/results.json').write_text(json.dumps(raw))
        with self.assertRaises(ValueError):self.store.show(result['run_id'])

    def test_qualified_cohort_uses_schema5_and_live_revocation_still_blocks(self):
        f=qualified.PreservationHistoryTests();f.setUp()
        try:
            plan=replace(f.spec,sequential=core.config(2,10,1))
            f.f.manifest['experiment']=plan.to_dict();f.f.write_manifest()
            result,_=f.f.run_history();self.assertEqual(result['state'],'sealed',result)
            analysis=f.f.store.analyze(result['run_id'])
            self.assertEqual(analysis['endpoints']['cohort:preserved']['exact_low'],[0,1])
            receipt=f.f.store.decision(result['run_id'])
            self.assertIn('cohort:preserved',receipt['receipt']['analysis_disposition']['required_endpoints'])
            f.f.revoke()
            with self.assertRaises(ValueError):f.f.store.verify_decision(result['run_id'],receipt['receipt_sha256'])
        finally:f.doCleanups()

    def test_legacy_workflow_refuses_before_controls(self):
        from agent_harness_lab.workflow import validate_manifest
        path=self.f.root/'workflow.json'
        path.write_text(json.dumps(dict(experiment={'sequential':self.plan.sequential},skills=[],harness_paths=[],
            tasks=[dict(id=split,split=split,fixture=split,grader=['/usr/bin/true'])
                for split in ('development','regression','heldout')],grader_self_tests=[])))
        with self.assertRaisesRegex(ValueError,'does not consume sequential'):
            validate_manifest(path,self.f.args.baseline,self.f.args.candidate,[],self.f.runner,lambda:30,set())

    def test_selected_protected_split_is_not_downgraded_by_development_plan(self):
        self.f.manifest['tasks'][0]['split']='heldout';self.install()
        with patch.object(SandboxBackend,'capability',side_effect=AssertionError('no protected probe')),self.assertRaisesRegex(ValueError,'development/regression'):
            self.f.runner.evaluate(self.f.args)
        with self.assertRaisesRegex(ValueError,'development/regression'):self.run_history()
        self.assertFalse(self.store.root.exists());self.assertFalse(self.calls)

    def test_json_or_wrong_input_view_controller_rejects_before_probe(self):
        f=self.f
        f.args.cgroup_root=f.root/'delegation';f.args.image_root=f.root/'image';f.args.image_sha256='a'*64;f.args.disk_bytes=65536
        f.args.output=f.root/'execution'
        controller=custody.Controller(self.plan,f.root/'sequential',time.monotonic()+30,inputs_sha256='0'*64)
        for forged in ({},controller):
            f.args._sequential_controller=forged
            with patch.object(SandboxBackend,'capability',side_effect=AssertionError('no foreign-view probe')),self.assertRaises(ValueError):
                f.runner.evaluate(f.args)
        with tempfile.TemporaryDirectory() as folder:
            control=custody.Controller(self.plan,Path(folder)/'ledger',time.monotonic()+30,inputs_sha256='0'*64)
            alias=copy(control);control.claim(self.plan,'0'*64)
            with self.assertRaises(ValueError):alias.claim(self.plan,'0'*64)

    def test_actual_responses_uses_fresh_calls_but_never_authenticates_model_or_assumptions(self):
        f=inference.ResponsesEvaluationTests();f.setUp()
        try:
            selected=self.f.manifest['tasks'];f.fixture.manifest['tasks']=[deepcopy(t) for t in selected]
            # Use its own complete export paths, not another fixture's absolute oracle locators.
            for task in f.fixture.manifest['tasks']:
                task['grader']=[v.replace(str(self.f.root),str(f.root)) for v in task['grader']]
            f.fixture.manifest['grader_bundles']={t['id']:f.fixture.manifest['grader_bundles'][t['id']] for t in selected}
            plan=replace(self.plan,baseline_hash=f.runner.digest(f.root/'baseline'),candidate_hash=f.runner.digest(f.root/'candidate'),
                grader_hash=identity(f.root/'export',f.fixture.manifest['tasks'],f.fixture.manifest['grader_bundles']))
            f.fixture.manifest['experiment']=plan.to_dict();f.fixture.write_manifest()
            with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=f.backend),patch.object(responses,'request',side_effect=f.transport):
                self.assertEqual(f.runner.evaluate(f.args),0)
            result=f.fixture.results();self.assertEqual((f.requests,f.commands,f.grades),(8,4,4))
            self.assertEqual(result['sequential_analysis']['assumptions_status'],'declared_unverified')
            self.assertEqual(result['sequential_analysis']['future_expected_quality'],'unknown')
        finally:f.doCleanups()


if __name__=='__main__':unittest.main()
