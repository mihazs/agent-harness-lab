"""Published conditional WoR math and fresh synthetic execution; no real models."""
from copy import copy, deepcopy
from dataclasses import replace
from fractions import Fraction
from decimal import Decimal,localcontext,getcontext
from contextlib import ExitStack
import itertools
import hashlib
import json
import math
from collections import Counter
import os
import random
from pathlib import Path
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import agent_harness_lab
from agent_harness_lab.evaluation import sequential as core, sequential_state as custody, responses, benchmark, oracle_history
from agent_harness_lab.evaluation.plan import ExperimentSpec
from agent_harness_lab.evaluation.statistics import TrialRecord,analyze as fixed_analyze
from agent_harness_lab.evaluation.precision import preflight
from agent_harness_lab.evaluation.oracle import identity
from agent_harness_lab.evaluation.oracle_history import HistoryStore
from agent_harness_lab.evaluation.sandbox import SandboxBackend
from tests.integration.evaluation import test_offline_evaluation as offline
from tests.integration.sandbox import test_sandbox_inference as inference
from tests.unit.evaluation import test_preservation_analysis as preservation
from tests.integration.evaluation import test_preservation_history as qualified
from tests.support.unittest_cleanups import run_cleanups


# Synthetic offline arms are real subprocesses, so their quotas are generous enough
# that host load cannot exhaust them (issue #179). Deadline enforcement itself is
# covered by explicit tests that exhaust a budget through an injected clock.
ARM_SECONDS=5
ROOT_SECONDS=60
HISTORY_SECONDS=180


def spec(n=16,**kwargs):
    return ExperimentSpec('synthetic-sequential',tuple((f't{i}',f'r{i}','family') for i in range(n)),1,13,
        2*n,2,.05,.1,.05,(('family',1),),'b'*64,'c'*64,'g'*64,'i'*64,
        sequential=core.config(n,3,.25),**kwargs)


def records(plan,binding,outcome):
    return [TrialRecord(r['trial_id'],outcome(r),'graded','executable_grader',
        plan.baseline_hash,plan.candidate_hash,plan.grader_hash,plan.runtime_hash) for r in core.schedule(plan,binding)]


class TestSequentialMath:
    def test_selected_import_root(self):
        expected=os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
        if expected:assert Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve())

    def test_positive_subnormal_alpha_survives_family_allocation(self):
        plan=replace(spec(3),alpha=math.ulp(0.0),
            tasks=(('a','ra','first'),('b','rb','second'),('c','rc','third')),
            family_weights=(('first',.2),('second',.3),('third',.5)))
        plan.validate();binding=core.create_binding(plan,[])
        core.validate_binding(plan,binding)
        bounds=core.endpoint([Fraction(1)],1,core.rational(plan.alpha)/3,binding['lambdas'])
        assert bounds[:2] == (Fraction(1),Fraction(1))

    def test_uniform_fisher_yates_is_not_a_user_seeded_shuffle(self):
        plan=spec(3);orders=set()
        for first in range(3):
            for second in range(2):
                with patch.object(core.secrets,'randbelow',side_effect=[first,second]) as draw:
                    binding=core.create_binding(plan,[])
                assert [c.args[0] for c in draw.call_args_list] == [3,2];orders.add(tuple(binding['root_order']))
        assert len(orders) == 6

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
        assert probability <= .01, wrong
        one=core.analyze(plan,records(plan,binding,lambda row:row['version']=='candidate'),binding)
        repeated=replace(plan,repeats=20,max_trials=1280,sequential=core.config(32,100,1))
        other=core.create_binding(repeated,[]);other['root_order']=binding['root_order']
        many=core.analyze(repeated,records(repeated,other,lambda row:row['version']=='candidate'),other)
        assert one['endpoints'] == many['endpoints'];assert many['roots'] == 32

    def test_absent_mode_preserves_exact_legacy_plan_and_schedule_bytes(self):
        from agent_harness_lab.evaluation.plan import schedule
        from agent_harness_lab.private_journal import encoded
        plan=replace(spec(),sequential=None)
        assert 'sequential' not in plan.to_dict()
        assert plan.digest == '27bb105305f8a9643a0ac5966399d7f20f55bcf6da7d1c2011aec02937bb3bff'
        assert hashlib.sha256(encoded(schedule(plan))).hexdigest() == 'ebbac77c4f7ae92ee840edf22234f924e54584ed97d9a626a7d77d6159a0c398'

    def test_logarithm_enclosure_is_outward_and_context_local(self):
        before=getcontext().copy()
        for alpha in (Fraction(1,20),Fraction(1,20640),Fraction(1,10**300)):
            upper=core.log_upper(alpha)
            with localcontext() as context:
                context.prec=100
                reference=(Decimal(2*alpha.denominator)/Decimal(alpha.numerator)).ln()
                assert (Decimal(upper.numerator)/Decimal(upper.denominator)) > reference
        assert getcontext().prec == before.prec

    def test_reference_formula_and_exact_census(self):
        plan=spec();binding=core.create_binding(plan,[])
        for alpha in (Fraction(1,20),Fraction(1,60)):
            values=[Fraction(-1),Fraction(0),Fraction(1),Fraction(1)]
            result=core.endpoint(values,4,alpha,binding['lambdas'])
            assert result[:2] == (Fraction(1,4),Fraction(1,4))
            # Direct equation3.2–3.7 calculation independent of core.endpoint.
            s=a=b=q=0.
            for i,x in enumerate(values[:2],1):
                lam=float(core.unpack(binding['lambdas'][i-1]));a+=lam*(float(x)+s/(4-i+1));b+=lam*4/(4-i+1);q+=lam*lam/2;s+=float(x)
            low=max(-.75,a/b-(q+math.log(2/float(alpha)))/b)
            hi=min(.25,a/b+(q+math.log(2/float(alpha)))/b)
            actual=core.endpoint(values[:2],4,alpha,binding['lambdas'])
            assert round(abs(float(actual[0])-low),12)==0;assert round(abs(float(actual[1])-hi),12)==0

    def test_all_six_exact_margin_orders_and_weighted_primary(self):
        plan=replace(spec(4),tasks=(('a','x','family'),('b','y','family'),('c','z','family'),('d','z','family')),
            repeats=10,max_trials=80,sequential=core.config(3,100,1))
        binding=core.create_binding(plan,[])
        def outcome(row):
            if row['task'] in ('a','b'):return row['version']=='candidate' and row['repetition']==0
            return row['task']=='c' and row['version']=='baseline' and row['repetition']==0
        for order in itertools.permutations(('x','y','z')):
            binding['root_order']=list(order);result=core.analyze(plan,records(plan,binding,outcome),binding)
            assert result['primary']['exact_low'] == [1,20];assert result['status'] != 'supported'
        # Exact normalized decimal weights and NI equality have no float upgrade.
        plan=replace(spec(4),tasks=(('a','x','one'),('b','y','one'),('c','z','two'),('d','w','two')),
            family_weights=(('one',.1),('two',.2)),alpha=.99)
        # Existing plan requires normalized weights; choose exact decimal thirds with sum1.
        plan=replace(plan,family_weights=(('one',.4),('two',.6)))
        binding=core.create_binding(plan,[])
        result=core.analyze(plan,records(plan,binding,lambda row:row['family']=='one' and row['version']=='candidate'),binding)
        assert result['primary']['exact_low'] == [2,5];assert result['family_weights']['two'] == [3,5]

    def test_ni_equality_and_cohort_member_task_mean_differs_from_whole_root(self):
        plan=replace(spec(4),repeats=10,max_trials=80,sequential=core.config(4,100,1),family_margin=.1)
        binding=core.create_binding(plan,[])
        data=records(plan,binding,lambda row:row['version']=='baseline' and row['repetition']==0)
        result=core.analyze(plan,data,binding)
        assert result['endpoints']['family:family']['exact_low'] == [-1,10]
        assert result['endpoints']['family:family']['disposition'] == 'supported'
        assert result['status'] != 'supported' # NI alone is not practical superiority.
        plan=replace(spec(4),tasks=(('t0','r0','family'),('t1','r0','family'),('t2','r1','family'),('t3','r1','family')),
            sequential=core.config(2,10,1))
        plan,frozen=preservation.freeze(replace(plan,cohort_manifest_sha256='d'*64),members=['t0','t2'])
        binding=core.create_binding(plan,core.membership(plan,frozen['manifest_json']))
        data=records(plan,binding,lambda row:row['version']==('baseline' if row['task'] in ('t0','t2') else 'candidate'))
        result=core.analyze(plan,data,binding,cohort_json=frozen['manifest_json'])
        assert result['primary']['exact_low'] == [0,1]
        assert result['endpoints']['cohort:protected']['exact_high'] == [-1,1]

    def test_sampling_budget_does_not_expand_population_or_repeats(self):
        plan=replace(spec(100),max_trials=4,sequential=core.config(2,3,.25))
        plan.validate();binding=core.create_binding(plan,[])
        assert len(core.schedule(plan,binding)) == 4
        result=core.analyze(plan,records(plan,binding,lambda row:row['version']=='candidate'),binding)
        assert result['endpoints']['family:family']['population_roots'] == 100
        assert result['unobserved_roots'] == 98

    def test_sparse_exact_family_harm_precedes_favorable_minimum(self):
        plan=replace(spec(3),tasks=(('a','x','common'),('b','y','common'),('c','z','rare')),
            family_weights=(('common',.8),('rare',.2)))
        binding=core.create_binding(plan,[])
        data=records(plan,binding,lambda row:row['version']==('baseline' if row['family']=='rare' else 'candidate'))
        result=core.analyze(plan,data,binding)
        assert result['status'] == 'rejected'
        assert result['endpoints']['family:rare']['exact_high'] == [-1,1]
        assert result['endpoints']['family:rare']['disposition'] == 'harm'
        data=records(plan,binding,lambda row:row['family']=='common' and row['version']=='candidate')
        result=core.analyze(plan,data,binding)
        assert result['status'] == 'inconclusive'
        assert result['endpoints']['family:rare']['disposition'] == 'insufficient_roots'

    def test_sparse_required_cohort_exact_harm_precedes_minimum(self):
        plan,frozen=preservation.freeze(replace(spec(3),cohort_manifest_sha256='d'*64),members=['t0'])
        binding=core.create_binding(plan,core.membership(plan,frozen['manifest_json']))
        data=records(plan,binding,lambda row:row['version']==('baseline' if row['task']=='t0' else 'candidate'))
        result=core.analyze(plan,data,binding,cohort_json=frozen['manifest_json'])
        assert result['status'] == 'rejected'
        assert result['endpoints']['cohort:protected']['exact_high'] == [-1,1]
        assert result['endpoints']['cohort:protected']['disposition'] == 'harm'

    def test_subset_cohort_means_and_preservation_veto(self):
        base=replace(preservation.plan(100),sequential=core.config(100,3,.25))
        plan,frozen=preservation.freeze(base,members=[t[0] for t in base.tasks[:20]])
        binding=core.create_binding(plan,core.membership(plan,frozen['manifest_json']))
        data=records(plan,binding,lambda row:(int(row['task'][1:])<20)==(row['version']=='baseline'))
        result=core.analyze(plan,data,binding,cohort_json=frozen['manifest_json'])
        assert result['primary']['exact_low'] == [3,5];assert result['status'] == 'rejected'
        assert result['endpoints']['cohort:protected']['exact_high'] == [-1,1]
        assert result['multiplicity']['endpoints'] == 2

    def test_binding_plan_bounds_prefix_duplicates_and_fixed_consumers(self):
        plan=spec();binding=core.create_binding(plan,[]);data=records(plan,binding,lambda row:row['version']=='candidate')
        assert core.analyze(plan,data[:1],binding)['status'] == 'incomplete'
        assert core.analyze(plan,[data[0],data[0]],binding)['status'] == 'incomplete'
        assert fixed_analyze(plan,data)['reason'] == 'owned_sequential_inspection_required'
        with pytest.raises(ValueError):preflight(plan)

    @pytest.mark.parametrize('changes',[
        pytest.param(lambda plan:dict(precision_half_width=.3),id='precision-half-width'),
        pytest.param(lambda plan:dict(profile='certification',partition='certification'),id='certification-profile'),
        pytest.param(lambda plan:dict(sequential={**plan.sequential,'max_roots':True}),id='boolean-max-roots'),
        pytest.param(lambda plan:dict(sequential={**plan.sequential,'arm_seconds':float('nan')}),id='nan-arm-seconds'),
    ])
    def test_plan_bounds_reject_invalid_sequential_and_precision_changes(self,changes):
        plan=spec()
        with pytest.raises(ValueError):replace(plan,**changes(plan)).validate()

    def test_schedule_is_binding_order_invariant_and_forged_bindings_are_rejected(self):
        plan=spec();binding=core.create_binding(plan,[])
        original=core.schedule(plan,binding)
        alternate=deepcopy(binding);alternate['root_order'].reverse()
        first={(r['root'],r['task'],r['repetition']):[] for r in original}
        second=deepcopy(first)
        for row in original:first[row['root'],row['task'],row['repetition']].append(row['version'])
        for row in core.schedule(plan,alternate):second[row['root'],row['task'],row['repetition']].append(row['version'])
        assert first == second
        for forged in ({},dict(binding,nonce='x'),dict(binding,root_order=[binding['root_order'][0]]*16)):
            with pytest.raises((ValueError,TypeError)):core.validate_binding(plan,forged)

    def test_empty_intersection_never_restarts_and_shared_shock_is_not_expected_quality(self):
        # An extreme but possible alpha-error path can empty a running intersection.
        tuning=[[1,2]]*100
        assert core.endpoint([Fraction(1)]*50+[Fraction(-1)]*50,100,Fraction(99,100),tuning) is None
        plan=spec();binding=core.create_binding(plan,[])
        for shock in (True,False):
            result=core.analyze(plan,records(plan,binding,lambda row:(row['version']=='candidate')==shock),binding)
            assert result['status'] == ('supported' if shock else 'rejected')
            assert result['future_expected_quality'] == 'unknown'
            assert not result['automatic_promotion']


class SequentialOnline:
    """Fresh synthetic sequential history builder; test_sequential_kernel builds it through setUp() and doCleanups()."""
    def setUp(self):
        self._cleanups=ExitStack()
        self.f=offline.OfflineFixture();self.f.setUp();self._cleanups.callback(run_cleanups,self.f)
        all_tasks=self.f.manifest['tasks'];seen=set();tasks=[]
        for task in all_tasks:
            if task['root'] not in seen:tasks.append(task);seen.add(task['root'])
        tasks=tasks[:3]
        for task in tasks:task['family']='family'
        self.f.manifest['tasks']=tasks
        self.f.manifest['grader_bundles']={t['id']:self.f.manifest['grader_bundles'][t['id']] for t in tasks}
        self.plan=ExperimentSpec('synthetic-fresh-online',tuple((t['id'],t['root'],t['family']) for t in tasks),1,13,6,2,
            .05,.1,.05,(('family',1),),self.f.runner.digest(self.f.root/'baseline'),self.f.runner.digest(self.f.root/'candidate'),
            identity(self.f.root/'export',tasks,self.f.manifest['grader_bundles']),'a'*64,sequential=core.config(3,ROOT_SECONDS,ARM_SECONDS))
        self.install()
        self.store=HistoryStore(self.f.root/'history');self.calls=[]

    def doCleanups(self):self._cleanups.close()

    def install(self):self.f.manifest['experiment']=self.plan.to_dict();self.f.write_manifest()

    def execute(self,*args,**kwargs):
        self.calls.append(args[3]);run=self.f.emulate(*args)
        run['attestation']=dict(backend='bubblewrap-offline-v1',role=args[3],network='none',aggregate_disk_bound_verified=True,
            cgroup_v2={'empty_after_cleanup':True},runtime_image=dict(sha256='a'*64,verified_before=True,verified_after=True))
        return run

    def run_history(self,execute=None,budget=HISTORY_SECONDS,*,expect='sealed'):
        started=time.monotonic()
        with patch.object(SandboxBackend,'capability',return_value={'status':'available'}),patch.object(SandboxBackend,'run',side_effect=execute or self.execute):
            result=self.store.run(self.f.args.manifest,self.f.args.baseline,self.f.args.candidate,cgroup_root=self.f.root/'delegation',
                image_root=self.f.root/'image',image_sha256='a'*64,disk_bytes=65536,budget_seconds=budget)
        if expect is not None and result['state']!=expect:
            raise AssertionError(f"history run expected {expect}, got {result['state']}: "+json.dumps(self.run_diagnostic(result,budget,time.monotonic()-started),sort_keys=True))
        return result

    def run_diagnostic(self,result,budget,elapsed):
        """Report why a run did not seal from the store's own non-private custody fields."""
        case=self.store.case(result['run_id']);value=dict(state=result['state'],budget_seconds=budget,elapsed_seconds=round(elapsed,3),
            dispatched_roles=dict(Counter(self.calls)),quotas={k:self.plan.sequential[k] for k in ('root_seconds','arm_seconds')})
        with self.store.locked():event=self.store.history(case)[-1][0]
        value['last_event']=dict(kind=event['kind'],state=event['state'],reason=event['payload'].get('reason'))
        terminal=case/'sequential/terminal.json'
        if terminal.exists():value['controller_stop']={k:json.loads(terminal.read_text()).get(k) for k in ('status','reason','completed_roots')}
        results=case/'execution/results.json'
        if results.exists():
            raw=json.loads(results.read_text());analysis=raw.get('sequential_analysis') or {}
            value['trial_statuses']=dict(Counter(t.get('status') for t in raw.get('trials',[])))
            value['trial_errors']=dict(Counter('subprocess_timeout' if 'timed out' in str(t['error']) else 'other_error'
                for t in raw.get('trials',[]) if t.get('error')))
            value['analysis']=dict(status=analysis.get('status'),reason=analysis.get('reason'),completed_roots=analysis.get('completed_roots'))
        return value

    def skewed_clock(self,module):
        """Advance only one deadline owner's monotonic clock; enforcement code is unchanged."""
        skew=[0.0];real=time.monotonic
        self._cleanups.enter_context(patch.object(module,'time',SimpleNamespace(monotonic=lambda:real()+skew[0])))
        return skew


class TestSequentialOnline(SequentialOnline):
    @pytest.fixture(autouse=True)
    def _online(self,tmp_path):
        self.tmp_path=tmp_path
        self.setUp()
        yield
        self.doCleanups()

    def test_actual_fresh_both_arms_stop_early_and_guarded_receipt_replays_without_calls(self):
        result=self.run_history();assert result['state'] == 'sealed', result
        assert self.calls.count('executor') == 4;assert self.calls.count('grader') == 4
        analysis=self.store.analyze(result['run_id']);assert analysis['status'] == 'supported'
        assert analysis['completed_roots'] == 2;assert analysis['unobserved_roots'] == 1
        receipt=self.store.decision(result['run_id']);assert not receipt['adoption_authority']
        with patch.object(SandboxBackend,'run',side_effect=AssertionError('must not resume or rerun')):
            assert self.store.verify_decision(result['run_id'],receipt['receipt_sha256']) == receipt
        case=self.store.case(result['run_id']);rows=json.loads((case/'execution/results.json').read_text())['trials']
        assert {t['version'] for t in rows} == {'baseline','candidate'}
        with pytest.raises(ValueError):self.store.historical_plan(result['run_id'])
        # Reusing the same hypothesis never reuses rows or controller sampling custody.
        again=self.run_history();assert again['run_id'] != result['run_id']
        other=json.loads((self.store.case(again['run_id'])/'execution/results.json').read_text())['trials']
        assert {r['trial_id'] for r in rows}.isdisjoint(r['trial_id'] for r in other)

    def test_caller_ceiling_admits_only_frozen_sequential_root_budget(self):
        self.plan=replace(self.plan,max_trials=4,sequential=core.config(2,ROOT_SECONDS,ARM_SECONDS))
        self.f.manifest['max_trials']=4;self.install()
        result=self.run_history();assert result['state'] == 'sealed', result
        assert self.calls.count('executor') == 4;assert self.calls.count('grader') == 4
        assert self.store.analyze(result['run_id'])['completed_roots'] == 2
        self.plan=replace(self.plan,max_trials=6,sequential=None);self.install()
        with (patch.object(SandboxBackend,'capability',side_effect=AssertionError('no fixed-mode probe')),
                pytest.raises(ValueError, match='trial limit exceeded')):
            self.f.runner.evaluate(self.f.args)

    def test_harm_insufficient_and_no_root_budget_have_truthful_dispositions(self):
        first=self.f.root/'baseline/solve.py';second=self.f.root/'candidate/solve.py'
        a,b=first.read_bytes(),second.read_bytes();first.write_bytes(b);second.write_bytes(a)
        self.plan=replace(self.plan,baseline_hash=self.f.runner.digest(first.parent),candidate_hash=self.f.runner.digest(second.parent));self.install()
        result=self.run_history();assert result['state'] == 'sealed', result
        assert self.store.analyze(result['run_id'])['status'] == 'rejected'
        assert self.calls.count('executor') == 4
        self.calls.clear();self.store=HistoryStore(self.f.root/'budget-history')
        # A root quota equal to the whole history budget can never be reserved once any time has passed.
        self.plan=replace(self.plan,sequential=core.config(3,HISTORY_SECONDS,ARM_SECONDS));self.install()
        result=self.run_history();assert result['state'] == 'sealed', result
        assert self.calls == [];assert self.store.analyze(result['run_id'])['reason'] == 'root_budget_not_reserved'

    def test_exhausted_root_quota_is_incomplete_never_sealed(self):
        skew=self.skewed_clock(custody)
        def execute(*args,**kwargs):
            if args[3]=='executor':skew[0]=ROOT_SECONDS
            return self.execute(*args,**kwargs)
        result=self.run_history(execute,expect='incomplete')
        assert self.calls == ['executor','grader']
        terminal=json.loads((self.store.case(result['run_id'])/'sequential/terminal.json').read_text())
        assert (terminal['status'],terminal['reason']) == ('incomplete','partial_root_budget')
        assert self.store.analyze(result['run_id'])['reason'] == 'controller_run_not_sealed'

    def test_exhausted_history_budget_after_execution_is_incomplete_never_sealed(self):
        skew=self.skewed_clock(oracle_history)
        def execute(*args,**kwargs):
            skew[0]=HISTORY_SECONDS;return self.execute(*args,**kwargs)
        result=self.run_history(execute,expect='incomplete')
        assert self.calls.count('executor') == 4
        case=self.store.case(result['run_id'])
        assert not (case/'evidence.json').exists()
        assert self.store.analyze(result['run_id'])['reason'] == 'controller_run_not_sealed'

    def test_unsealed_fixture_run_reports_its_reason_instead_of_a_downstream_failure(self):
        skew=self.skewed_clock(custody)
        def execute(*args,**kwargs):
            if args[3]=='executor':skew[0]=ROOT_SECONDS
            return self.execute(*args,**kwargs)
        with pytest.raises(AssertionError) as failure:self.run_history(execute)
        message=str(failure.value)
        for fragment in ('expected sealed, got incomplete','"reason": "partial_root_budget"',
                '"dispatched_roles": {"executor": 1, "grader": 1}','"budget_seconds": 180','execution_or_evidence_incomplete'):
            assert fragment in message

    def test_missing_arm_and_interruption_are_incomplete_not_resumed(self):
        def missing(*args,**kwargs):
            if len(self.calls)==2:raise OSError('synthetic second arm unavailable')
            return self.execute(*args,**kwargs)
        result=self.run_history(missing,expect='incomplete')
        assert self.store.analyze(result['run_id'])['status'] != 'supported'
        assert self.calls.count('executor') <= 1

    def test_real_interrupt_retains_attempt_and_cold_restart_never_executes_it(self):
        def interrupt(*args,**kwargs):
            if args[3]=='executor':raise KeyboardInterrupt()
            return self.execute(*args,**kwargs)
        with pytest.raises(KeyboardInterrupt):self.run_history(interrupt)
        cases=[p for p in (self.store.root/'cases').iterdir() if (p/'inputs.json').exists()]
        assert len(cases) == 1;case=cases[0]
        assert self.store.show(case.name)['state'] == 'incomplete'
        assert (case/'sequential/0000-started.json').exists()
        assert not (case/'sequential/0000-inspected.json').exists()
        with pytest.raises(FileExistsError):custody.Controller(self.plan,case/'sequential',time.monotonic()+HISTORY_SECONDS,inputs_sha256='0'*64)
        assert self.calls == []

    def test_revocation_blocks_current_decision_without_new_execution(self):
        result=self.run_history();receipt=self.store.decision(result['run_id'])
        bundle=result['dependencies'][0]
        path=self.f.root/'quarantine.json'
        path.write_text(json.dumps(dict(schema_version=1,bundle_sha256=bundle,
            expected_event_sha256=self.store.oracle(bundle)['event_sha256'],
            operator=dict(identity='synthetic-reviewer',source='operator_attestation',provenance='public synthetic',basis='reference defect'),
            evidence_refs=[dict(ref='synthetic:defect',sha256='d'*64)],reason='synthetic oracle invalidation')))
        self.store.quarantine(path)
        with pytest.raises(ValueError):self.store.verify_decision(result['run_id'],receipt['receipt_sha256'])
        assert self.store.analyze(result['run_id'])['status'] == 'blocked'
        with patch.object(SandboxBackend,'run',side_effect=AssertionError('no quarantined dispatch')),pytest.raises(ValueError):self.run_history()

    def test_changed_sampling_or_inspection_seal_fails_closed(self):
        result=self.run_history();case=self.store.case(result['run_id'])
        binding=case/'sequential/binding.json';raw=json.loads(binding.read_text());raw['root_order'].reverse();binding.write_text(json.dumps(raw))
        with pytest.raises(ValueError):self.store.show(result['run_id'])

    def test_frozen_remaining_quota_not_reduced_and_insufficient_family_stays_inconclusive(self):
        tasks=self.f.manifest['tasks']
        for i,task in enumerate(tasks):task['family']='rare' if i==0 else 'common'
        self.plan=replace(self.plan,tasks=tuple((t['id'],t['root'],t['family']) for t in tasks),
            family_weights=(('rare',.5),('common',.5)),sequential=core.config(2,ROOT_SECONDS,ARM_SECONDS),max_trials=4);self.install()
        limits=[];grade_budgets=[]
        from agent_harness_lab.evaluation import bank_tools
        original=bank_tools.regrade_artifacts
        def execute(*args,**kwargs):
            limits.append((args[3],args[4].seconds));return self.execute(*args,**kwargs)
        def grade(*args,**kwargs):
            grade_budgets.append(kwargs['limits'].seconds);return original(*args,**kwargs)
        with patch.object(bank_tools,'regrade_artifacts',side_effect=grade):result=self.run_history(execute)
        assert result['state'] == 'sealed', result
        analysis=self.store.analyze(result['run_id']);assert analysis['status'] == 'inconclusive'
        assert analysis['reason'] == 'maximum_roots';assert len(limits) == 8
        assert {seconds for role,seconds in limits if role=='executor'} == {ARM_SECONDS}
        assert grade_budgets == [ARM_SECONDS]*4
        assert all(0<seconds<=ARM_SECONDS for _,seconds in limits)

    def test_engine_change_and_expired_direct_parent_cannot_continue(self):
        result=self.run_history();run_id=result['run_id']
        with patch.object(core,'engine_pin',return_value='f'*64),pytest.raises(ValueError):self.store.analyze(run_id)
        # The public runner preserves the legacy already-exhausted status before sampling.
        self.f.args.parent_deadline=-1
        with patch.object(SandboxBackend,'capability',side_effect=AssertionError('no probe')),patch.object(SandboxBackend,'run',side_effect=AssertionError('no worker')):
            assert self.f.runner.evaluate(self.f.args) == 1
        assert self.f.results()['reason'] == 'preflight_budget_exhausted'
        assert not (self.f.root/'output/sequential').exists()

    def test_live_sampling_and_prefix_mutation_are_detected_before_next_worker(self):
        seen=[]
        original=custody.Controller.before
        def before(control,scheduled,rows):
            if len(rows)==2 and not seen:
                seen.append(True);path=control.path/'0000-inspected.json'
                # Complete root is not inspected until this boundary; first inspect then mutate.
                control._inspect(rows);path.write_text('{}')
            return original(control,scheduled,rows)
        with patch.object(custody.Controller,'before',before):self.run_history(expect='incomplete')
        assert self.calls.count('executor') <= 2

    def test_critical_partial_veto_and_no_from_json_controller(self):
        plan=self.plan
        for seed in range(100):
            chosen=replace(plan,seed=seed)
            if core.schedule(chosen)[0]['version']=='candidate':plan=chosen;break
        with patch.object(core.secrets,'randbelow',side_effect=lambda n:n-1):
            control=custody.Controller(plan,self.tmp_path/'ledger',time.monotonic()+HISTORY_SECONDS,inputs_sha256='0'*64)
        schedule=control.schedule
        first=next(i for i,r in enumerate(schedule) if r['version']=='candidate')
        data=[]
        for row in schedule[:first+1]:
            data.append(dict(row,status='failed',grader=dict(outcome=False),
                controller_grade=dict(outcome=False,valid=True,critical_violation=row['version']=='candidate')))
        assert control.before(schedule[0],[])
        # The controller observes actual candidate critical evidence before a full root.
        control._inspect(data);result=control.finish(data)
        assert result['status'] == 'rejected';assert result['reason'] == 'critical_regression'
        assert first == 0;assert result['completed_roots'] == 0
        with pytest.raises(ValueError):control.validate(plan)
        with pytest.raises(FileExistsError):custody.Controller(plan,control.path,time.monotonic()+HISTORY_SECONDS,inputs_sha256='0'*64)
        with pytest.raises(AttributeError):custody.Controller.from_json(control.binding)

    def test_private_labels_cannot_publish_or_authenticate_a_saved_result(self):
        result=self.run_history();case=self.store.case(result['run_id']);raw=json.loads((case/'execution/results.json').read_text())
        raw.update(publication_allowed=True,provenance=dict(source_kind='synthetic',source_ref='synthetic:sequential',
            approved_publication=True,derived_from_private=False))
        assert not benchmark.public_export_allowed([raw])
        assert not benchmark.public_export_allowed([{**raw['sequential_analysis'],'publication_allowed':True,'provenance':raw['provenance']}])
        raw['trials'][0]['grader']['outcome']=not raw['trials'][0]['grader']['outcome']
        (case/'execution/results.json').write_text(json.dumps(raw))
        with pytest.raises(ValueError):self.store.show(result['run_id'])

    def test_qualified_cohort_uses_schema5_and_live_revocation_still_blocks(self):
        f=qualified.PreservationHistoryTests();f.setUp()
        try:
            plan=replace(f.spec,sequential=core.config(2,10,1))
            f.f.manifest['experiment']=plan.to_dict();f.f.write_manifest()
            result,_=f.f.run_history();assert result['state'] == 'sealed', result
            analysis=f.f.store.analyze(result['run_id'])
            assert analysis['endpoints']['cohort:preserved']['exact_low'] == [0,1]
            receipt=f.f.store.decision(result['run_id'])
            assert 'cohort:preserved' in receipt['receipt']['analysis_disposition']['required_endpoints']
            f.f.revoke()
            with pytest.raises(ValueError):f.f.store.verify_decision(result['run_id'],receipt['receipt_sha256'])
        finally:run_cleanups(f)

    def test_legacy_workflow_refuses_before_controls(self):
        from agent_harness_lab.workflow import validate_manifest
        path=self.f.root/'workflow.json'
        path.write_text(json.dumps(dict(experiment={'sequential':self.plan.sequential},skills=[],harness_paths=[],
            tasks=[dict(id=split,split=split,fixture=split,grader=['/usr/bin/true'])
                for split in ('development','regression','heldout')],grader_self_tests=[])))
        with pytest.raises(ValueError, match='does not consume sequential'):
            validate_manifest(path,self.f.args.baseline,self.f.args.candidate,[],self.f.runner,lambda:30,set())

    def test_selected_protected_split_is_not_downgraded_by_development_plan(self):
        self.f.manifest['tasks'][0]['split']='heldout';self.install()
        with patch.object(SandboxBackend,'capability',side_effect=AssertionError('no protected probe')),pytest.raises(ValueError, match='development/regression'):
            self.f.runner.evaluate(self.f.args)
        with pytest.raises(ValueError, match='development/regression'):self.run_history()
        assert not self.store.root.exists();assert not self.calls

    def test_json_or_wrong_input_view_controller_rejects_before_probe(self):
        f=self.f
        f.args.cgroup_root=f.root/'delegation';f.args.image_root=f.root/'image';f.args.image_sha256='a'*64;f.args.disk_bytes=65536
        f.args.output=f.root/'execution'
        controller=custody.Controller(self.plan,f.root/'sequential',time.monotonic()+HISTORY_SECONDS,inputs_sha256='0'*64)
        for forged in ({},controller):
            f.args._sequential_controller=forged
            with patch.object(SandboxBackend,'capability',side_effect=AssertionError('no foreign-view probe')),pytest.raises(ValueError):
                f.runner.evaluate(f.args)
        control=custody.Controller(self.plan,self.tmp_path/'ledger',time.monotonic()+HISTORY_SECONDS,inputs_sha256='0'*64)
        alias=copy(control);control.claim(self.plan,'0'*64)
        with pytest.raises(ValueError):alias.claim(self.plan,'0'*64)

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
                assert f.runner.evaluate(f.args) == 0
            result=f.fixture.results();assert (f.requests,f.commands,f.grades) == (8,4,4)
            assert result['sequential_analysis']['assumptions_status'] == 'declared_unverified'
            assert result['sequential_analysis']['future_expected_quality'] == 'unknown'
        finally:run_cleanups(f)

