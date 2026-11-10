"""Actual public controller receipt consumption; no provider or credential is used."""
from contextlib import ExitStack,redirect_stdout
from copy import copy
from dataclasses import asdict,replace
import gc
import io
import json
import os
import signal
from pathlib import Path
from types import SimpleNamespace
import time
from unittest.mock import Mock,patch

import pytest

from agent_harness_lab import cli,workflow
from agent_harness_lab.evaluation import controller_receipts as owned,permission_audit,responses
from agent_harness_lab.evaluation.permission_audit import PROFILE,Observation
from agent_harness_lab.promotion import policy,prerequisites
from agent_harness_lab.sources.controller import SourceSink
from agent_harness_lab.sources.schema import digest
from tests.integration.evaluation import test_evaluation_foundation as math_fixture
from tests.integration.sandbox import test_sandbox_kernel as kernel
from tests.integration.sandbox.test_sandbox_kernel import cgroup_root  # noqa: F401  (module-scoped pytest fixture of kernel.KernelCase)
from tests.integration.sandbox import test_sandbox_construction as roles
from tests.integration.workflow import test_workflow as workflow_fixture


class PublicCase:
    def assert_observed(self,scope,read):
        """Retain content-free FIRST-read evidence before assertion cleanup cancels scope."""
        evidence=dict(actual_scope=type(scope) is prerequisites._Scope)
        if evidence['actual_scope']:
            owner=prerequisites._STATES.get(scope)
            predicates=dict(active=prerequisites.current() is scope,open=not scope.closed,
                not_lost=not scope.lost,owned=owner is not None,
                same_process=owner is not None and owner[0]==os.getpid(),
                seal_matches=owner is not None and owner[2]==scope._state(),
                before_deadline=time.monotonic()<scope.deadline)
            events=scope.data['events']
            pending=next((row for row in events if not row['complete']),None)
            def label(value,allowed):return value if value in allowed else 'other'
            evidence.update(before_read=predicates,
                first_failed_scope_predicate=next((key for key,value in predicates.items() if not value),None),
                admitted=len(events),completed=sum(row['complete'] for row in events),
                first_incomplete=None if pending is None else dict(
                    role=label(pending['role'],('proposer','evaluator_author','executor','grader')),
                    stage=label(pending['stage'],('role','capability_probe'))))
        original=prerequisites._Scope.read
        reasons={message:code for code,message in (
            ('scope_unavailable','current unchanged live workflow scope required'),
            ('readback_unavailable','workflow readback budget or scope unavailable'),
            ('tree_readback_changed','workflow scope changed during tree readback'),
            ('baseline_changed','workflow baseline changed'),
            ('rootfs_changed','owned rootfs differs from selected workflow image'),
            ('harness_changed','owned role harness differs from frozen workflow'),
            ('binding_changed','workflow prerequisite binding changed'),
            ('input_readback_changed','workflow scope changed during input readback'))}
        def observed_read(item):
            try:return original(item)
            except (ValueError,OSError,TypeError,KeyError,AttributeError,RecursionError) as error:
                evidence.update(read_exception=type(error).__name__,
                    read_reason=reasons.get(str(error),'other'))
                raise
        with patch.object(prerequisites._Scope,'read',observed_read):packet=read(scope)
        assert packet['status'] == 'observed', json.dumps(evidence,sort_keys=True)
        return packet

    def run_public(self,*,mode='real',source=False,consumer=None,audited=True):
        fixture=workflow_fixture.WorkflowTests(self.addCleanup)
        case=self.root/('case-'+str(len(list(self.root.glob('case-*')))));case.mkdir()
        runtime=case/'runtime'
        if mode=='fake':runtime.mkdir();pin=dict(sha256='a'*64)
        else:pin=kernel.pinned_runtime(runtime)
        settings=dict(schema_version=1,model='synthetic-transport',credential_file=str(case/'nonexistent-credential'),
            max_requests=60,max_tool_calls=40,max_output_tokens=100,max_request_bytes=32768,max_response_bytes=16384)
        counter=[0];scopes=[];completions=[]
        def transport(credential,body,max_bytes,deadline):
            assert credential is None
            counter[0]+=1;ident=str(counter[0]);payload=json.loads(body)
            if any(row.get('type')=='function_call_output' for row in payload['input']):
                return roles.wire.reply([roles.wire.message('final-'+ident)])
            prompt=payload['input'][0]['content']
            if 'You are the proposer' in prompt:argv=['/usr/bin/python3','-c',roles.PROPOSER,'good']
            elif 'You are the evaluator author' in prompt:argv=['/usr/bin/python3','-c',roles.AUTHOR,'1','complete']
            else:argv=['/usr/bin/python3','-c',roles.EXECUTOR,'/inputs/harness']
            return roles.wire.reply([roles.wire.call(ident,arguments=json.dumps(dict(argv=argv)))])
        output=case/'public-run';text=io.StringIO()
        argv=['improve',str(fixture.project),'--agent','responses','--inference-settings',str(case/'nonexistent-settings'),
            '--cgroup-root',str(self.cgroup_root),'--image-root',str(runtime),'--image-sha256',pin['sha256'],
            '--disk-bytes','67108864','--timeout','90','--output',str(output)]
        if audited:argv.extend(['--permission-audit',PROFILE])
        original_report=policy.implementation_prerequisites
        def report(scope=None,**options):
            if scope is not None:scopes.append(scope)
            return consumer(scope,original_report) if consumer is not None else original_report(scope,**options)
        original_finish=owned._kernel_finished;lost=[False]
        def finish(ticket,*args):
            if ticket is not None and ticket.scope is not None and not lost[0] and mode in ('pending','exception'):
                lost[0]=True
                if mode=='exception':raise RuntimeError('synthetic lost completion')
                return None
            return original_finish(ticket,*args)
        original_completion=Observation._completion
        def completed(item,ticket,*args):
            if ticket.scope is not None:completions.append(ticket.nonce)
            return original_completion(item,ticket,*args)
        with ExitStack() as stack:
            stack.enter_context(patch.object(responses,'read_settings',return_value=(settings,'a'*64)))
            stack.enter_context(patch.object(responses,'read_credential',return_value=None))
            stack.enter_context(patch.object(responses,'request',side_effect=transport))
            stack.enter_context(patch.object(policy,'implementation_prerequisites',side_effect=report))
            stack.enter_context(patch.object(owned,'_kernel_finished',side_effect=finish))
            stack.enter_context(patch.object(Observation,'_completion',completed))
            if mode=='fake':
                helper=SimpleNamespace(root=case,roles=[])
                backend=Mock(permission_audit=PROFILE);backend.permission_reports=[]
                backend.capability.return_value=dict(status='available')
                backend.permission_qualification.side_effect=lambda:dict(profile=PROFILE,qualification='qualified')
                def execute(*args,**options):
                    run=roles.ControlledWorkflow.execute(helper,*args,**options)
                    audit=dict(status='complete',qualification='qualified',profile=PROFILE)
                    run['attestation']['permission_audit']=audit;backend.permission_reports.append(audit)
                    return run
                backend.run.side_effect=execute
                stack.enter_context(patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=backend))
            if source:
                sink=SourceSink(case/'source-journal.json',digest('synthetic-project'),digest('synthetic-scope'))
                original_improve=workflow.improve
                def with_source(args):args.source_sink=sink;return original_improve(args)
                stack.enter_context(patch.object(workflow,'improve',side_effect=with_source))
            with redirect_stdout(text):
                if mode=='exception':
                    with pytest.raises(RuntimeError, match='synthetic lost completion'):cli.main(argv)
                else:cli.main(argv)
        result=json.loads((output/'workflow.json').read_text())
        assert (fixture.project/'AGENTS.md').read_text() == 'bad'
        assert not result['automatic_promotion']
        assert prerequisites.current() is None
        if mode!='exception':assert counter[0] == 16
        return result,scopes,completions,output


class TestPolicyAuthority(PublicCase):
    @pytest.fixture(autouse=True)
    def _case(self,tmp_path):
        expected=os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
        if expected:assert Path(cli.__file__).resolve().is_relative_to(Path(expected).resolve())
        self.root=tmp_path
        self.cgroup_root=self.root/'unused-delegation'
        with ExitStack() as cleanups:
            self.addCleanup=cleanups.callback
            yield

    def test_failure_diagnostics_preserve_pre_read_state_without_content(self):
        scope=prerequisites._begin(self.root,'a'*64,time.monotonic()+30)
        self.addCleanup(scope.end)
        scope._admit('executor','role')
        with pytest.raises(AssertionError) as failure:
            self.assert_observed(scope,policy.implementation_prerequisites)
        message=str(failure.value)
        assert '"admitted": 1' in message
        assert '"completed": 0' in message
        assert '"role": "executor"' in message
        assert '"not_lost": true' in message
        assert str(self.root) not in message
        assert scope.nonce not in message
        scope.cancel()
        with pytest.raises(AssertionError) as failure:
            self.assert_observed(scope,policy.implementation_prerequisites)
        message=str(failure.value)
        assert '"first_failed_scope_predicate": "not_lost"' in message
        assert '"read_exception": "ValueError"' in message
        assert '"read_reason": "scope_unavailable"' in message

    def test_legacy_recommendation_inputs_are_declarations_without_implementation_authority(self):
        spec=math_fixture.plan(n=1000,repeats=1);analysis=math_fixture.analyze(spec,math_fixture.records(spec))
        decision=policy.decide(spec,dict(analysis,oracle_validity='current'),isolation_verified=True,
            effective_runtime_verified=True,graders_audited=True,lineage_reviewed=True,certification_reserved=True)
        assert decision.status == 'blocked';assert not decision.automatic
        assert decision.declared_prerequisites == {'isolation_verified':dict(status='declared_unverified',value=True)}
        packet=policy.implementation_prerequisites(declarations={'isolation_verified':True})
        assert packet['declared_prerequisites']['isolation_verified']['status'] == 'declared_unverified'
        assert all(row['status']=='unknown' for row in packet['checks'].values())

    def test_fully_labeled_fake_backend_cannot_issue_implementation_facts(self):
        result,_,_,_=self.run_public(mode='fake')
        assert result['status'] == 'blocked'
        assert result['reason'] == 'restricted_process_protocol_unqualified'
        assert 'recommendation' not in result
        assert result['isolation_qualification']['qualification'] == 'qualified'
        assert result['implementation_prerequisites']['status'] == 'unknown'
        assert all(row['status']=='unknown' for row in result['implementation_prerequisites']['checks'].values())

    @pytest.mark.parametrize('image',(pytest.param(None,id='none'),pytest.param({},id='empty-mapping'),pytest.param('invalid',id='not-a-digest')))
    def test_invalid_image_digest_cannot_begin_a_scope(self,image):
        baseline=self.root/'baseline';baseline.mkdir()
        with pytest.raises(ValueError):
            prerequisites._begin(baseline,image,time.monotonic()+5)
        assert prerequisites.current() is None

    def test_constructor_failures_and_mutated_fields_cannot_leak_scope_context(self):
        baseline=self.root/'baseline';baseline.mkdir()
        with patch.object(prerequisites._Scope,'_save',side_effect=RuntimeError('synthetic seal failure')):
            with pytest.raises(RuntimeError):prerequisites._begin(baseline,'a'*64,time.monotonic()+5)
        assert prerequisites.current() is None
        scope=prerequisites._begin(baseline,'a'*64,time.monotonic()+5)
        scope.deadline=object()
        assert policy.implementation_prerequisites(scope)['status'] == 'unknown'
        scope.end()
        assert prerequisites.current() is None

    def test_lost_scope_cannot_be_restored_by_clearing_lost_or_overriding_state(self):
        scope=self.synthetic_scope();assert policy.isolation_qualification(scope) == 'qualified'
        raw=prerequisites._STATES[scope][2]
        scope.data['events'][0]['boundary']=dict(status='qualified',forged=True)
        assert policy.isolation_qualification(scope) == 'unknown';assert scope.lost
        scope.lost=False;scope._save()
        assert policy.isolation_qualification(scope) == 'unknown'
        scope.lost=False;scope._state=lambda *args,**kwargs:raw
        assert policy.isolation_qualification(scope) == 'unknown'

    def synthetic_scope(self,*,boundary='qualified',permission='qualified'):
        """In-process synthetic producer facts; no kernel, provider or credential is used."""
        baseline=self.root/('baseline-'+str(len(list(self.root.glob('baseline-*')))));baseline.mkdir()
        scope=prerequisites._begin(baseline,'a'*64,time.monotonic()+30);self.addCleanup(scope.end)
        scope.data['events'].append(dict(ordinal=0,role='executor',stage='role',complete=True,image_sha256='a'*64,
            input_digests=dict(harness=scope.data['baseline_sha256']),boundary=dict(status=boundary),
            permission=None if permission is None else dict(qualification=permission)))
        scope.data['pairs'].append(dict(selected_paths=1));scope._save()
        return scope

    def certification_inputs(self):
        spec=math_fixture.plan(n=1000,repeats=1)
        return spec,dict(math_fixture.analyze(spec,math_fixture.records(spec)),oracle_validity='current')

    @pytest.mark.parametrize('forged',(pytest.param(True,id='bool'),pytest.param(1,id='int'),pytest.param('qualified',id='str'),
                                       pytest.param(object(),id='object')))
    def test_forged_isolation_value_has_no_effect_and_is_recorded_as_declared(self,forged):
        spec,analysis=self.certification_inputs()
        others=dict(effective_runtime_verified=True,graders_audited=True,lineage_reviewed=True,certification_reserved=True)
        baseline=policy.decide(spec,analysis,**others)
        decision=policy.decide(spec,analysis,isolation_verified=forged,**others)
        assert (decision.status,decision.reason,decision.isolation,decision.automatic) == (baseline.status,baseline.reason,'unknown',False)
        assert decision.status == 'blocked'
        assert decision.declared_prerequisites['isolation_verified']['status'] == 'declared_unverified'

    def test_forged_isolation_boolean_has_no_effect_and_is_recorded_as_declared(self):
        spec,analysis=self.certification_inputs()
        others=dict(effective_runtime_verified=True,graders_audited=True,lineage_reviewed=True,certification_reserved=True)
        baseline=policy.decide(spec,analysis,**others)
        assert baseline.declared_prerequisites == {}
        json.dumps(asdict(policy.decide(spec,analysis,isolation_verified=object(),**others)),allow_nan=False)

    def test_lost_oversized_copied_or_unqualified_scope_gives_unknown_isolation(self):
        spec,analysis=self.certification_inputs()
        others=dict(isolation_verified=True,effective_runtime_verified=True,graders_audited=True,
            lineage_reviewed=True,certification_reserved=True)
        def assert_unknown(scope):
            decision=policy.decide(spec,analysis,prerequisite_scope=scope,**others)
            assert (decision.status,decision.isolation) == ('blocked','unknown')
        oversized=self.synthetic_scope()
        oversized.data['synthetic_padding']='x'*prerequisites.MAX_BYTES;oversized._save()
        assert oversized.lost;assert prerequisites._STATES[oversized][2] is None
        assert_unknown(oversized);oversized.end()
        lost=self.synthetic_scope();lost.cancel();assert_unknown(lost);lost.end()
        live=self.synthetic_scope()
        assert_unknown(copy(live));assert_unknown(policy.implementation_prerequisites(live))
        assert_unknown(None);live.end();assert_unknown(live)

    @pytest.mark.parametrize('options',(pytest.param(dict(boundary='unknown'),id='boundary-unknown'),
        pytest.param(dict(permission='unknown'),id='permission-unknown'),pytest.param(dict(permission=None),id='permission-missing'),
        pytest.param(dict(permission='violated'),id='permission-violated')))
    def test_unqualified_boundary_or_permission_gives_unknown_isolation(self,options):
        spec,analysis=self.certification_inputs()
        scope=self.synthetic_scope(**options)
        decision=policy.decide(spec,analysis,prerequisite_scope=scope,isolation_verified=True,effective_runtime_verified=True,
            graders_audited=True,lineage_reviewed=True,certification_reserved=True)
        assert (decision.status,decision.isolation) == ('blocked','unknown')
        scope.end()

    @pytest.mark.parametrize('missing',('effective_runtime_verified','graders_audited','lineage_reviewed','certification_reserved'))
    def test_a_qualified_scope_with_one_missing_element_stays_blocked(self,missing):
        spec,analysis=self.certification_inputs();scope=self.synthetic_scope()
        others=('effective_runtime_verified','graders_audited','lineage_reviewed','certification_reserved')
        decision=policy.decide(spec,analysis,prerequisite_scope=scope,**{name:name!=missing for name in others})
        assert (decision.status,decision.isolation) == ('blocked','qualified')

    def test_qualified_scope_satisfies_only_the_isolation_element(self):
        spec,analysis=self.certification_inputs();scope=self.synthetic_scope()
        packet=policy.implementation_prerequisites(scope)
        assert policy.isolation_qualification(scope) == 'qualified'
        alone=policy.decide(spec,analysis,prerequisite_scope=scope)
        assert (alone.status,alone.reason,alone.isolation) == ('blocked','certification_prerequisites_unverified','qualified')
        others=('effective_runtime_verified','graders_audited','lineage_reviewed','certification_reserved')
        for blocked in (dict(analysis,oracle_validity='unverified'),dict(analysis,status='incomplete')):
            assert policy.decide(spec,blocked,prerequisite_scope=scope,**{name:True for name in others}).status == ('blocked' if blocked['status']!='incomplete' else 'incomplete')
        smoke=replace(spec,profile='descriptive_smoke')
        assert policy.decide(smoke,analysis,prerequisite_scope=scope,**{name:True for name in others}).status == 'inconclusive'
        decision=policy.decide(spec,analysis,prerequisite_scope=scope,**{name:True for name in others})
        assert (decision.status,decision.automatic) == ('recommended',False)
        assert all(row['status']=='unverified' for row in packet['mandatory_certification'].values())
        assert not packet['adoption_authority'];assert not packet['automatic_promotion']

    def test_known_private_profile_and_packet_cannot_be_relabelled_for_benchmark_export(self):
        from agent_harness_lab.evaluation.benchmark import public_export_allowed
        from tests.unit.evaluation import test_benchmark_contracts as fixtures
        packet=dict(policy.implementation_prerequisites(),publication_allowed=True)
        assert not public_export_allowed([{**fixtures.record(),'implementation_prerequisites':packet}])
        assert not public_export_allowed([{**fixtures.record(),'receipt':packet}])


@pytest.mark.skipif(not kernel.ENABLED,reason='explicit delegated kernel profile required')
class TestPublicControllerPrerequisite(PublicCase,kernel.KernelCase):
    def test_runtime_boundary_qualifies_actual_four_role_protocol_without_certification(self):
        result,_,completions,output=self.run_public(source=True)
        packet=result['implementation_prerequisites'];boundary=packet['restricted_process_protocol']
        assert boundary['status'] == 'qualified', boundary
        assert boundary['observations'] == packet['coverage']['admitted']
        assert set(boundary['roles']) == {'proposer','evaluator_author','executor','grader'}
        assert len(completions) == len(set(completions))
        assert all(row['status']=='unverified' for row in packet['mandatory_certification'].values())
        assert packet['native_identity'] == 'unverified';assert not packet['adoption_authority']
        trials=json.loads((output/'evaluation-0/results.json').read_text())['trials']
        assert all(row['source_capture']['status']=='observed' for row in trials)

    def test_runtime_boundary_missing_actual_startup_fact_blocks_passing_worker_claim(self):
        finish=owned._kernel_finished;removed=[]
        def missing(ticket,run,*args):
            if ticket is not None and ticket.scope is not None and ticket.stage=='role' and not removed:
                producer=owned._DISPATCHES[ticket]
                owned._DISPATCHES[ticket]=(*producer[:6],None,*producer[7:])
                run['attestation']['restricted_process_protocol']=dict(status='qualified')
                removed.append((run['status'],run['exit_code']))
            return finish(ticket,run,*args)
        with patch.object(owned,'_kernel_finished',side_effect=missing):result,_,_,_=self.run_public()
        assert removed == [('completed',0)]
        assert result['implementation_prerequisites']['restricted_process_protocol']['status'] == 'unknown'
        assert result['status'] == 'blocked';assert 'recommendation' not in result

    def test_real_public_all_role_workflow_shares_one_native_completion_with_source_and_rejects_replay(self):
        def consumer(scope,read):
            packet=self.assert_observed(scope,read)
            assert read(copy(scope))['status'] == 'unknown'
            assert read(packet)['status'] == 'unknown'
            if hasattr(os,'fork'):
                child=os.fork()
                if child==0:
                    try:
                        denied=read(scope)['status']=='unknown' and scope._admit('executor','role') is None
                    except BaseException:os._exit(2)
                    os._exit(0 if denied else 3)
                until=time.monotonic()+2;found=0
                while time.monotonic()<until:
                    found,status=os.waitpid(child,os.WNOHANG)
                    if found:break
                    time.sleep(.01)
                if not found:
                    os.kill(child,signal.SIGKILL);os.waitpid(child,0)
                    pytest.fail('forked workflow authority check exceeded its bound')
                assert os.waitstatus_to_exitcode(status) == 0, 'fork inherited workflow prerequisite authority'
                self.assert_observed(scope,read)
            return packet
        result,scopes,completions,output=self.run_public(source=True,consumer=consumer)
        packet=result['implementation_prerequisites'];assert result['status'] == 'inconclusive'
        assert packet['status'] == 'observed', packet
        assert set(packet['coverage']['roles']) == {'proposer','evaluator_author','executor','grader'}
        assert all(row['status']=='verified' for row in packet['checks'].values()), packet
        assert packet['permission_result_capture'] == 'qualified'
        assert packet['coverage']['capability_probes'] > 0
        assert all(value=='violated' for value in packet['capability_probe_permission_results'])
        assert len(completions) == packet['coverage']['admitted'];assert len(set(completions)) == len(completions)
        assert not packet['adoption_authority']
        assert all(row['status']=='unverified' for row in packet['mandatory_certification'].values())
        trials=json.loads((output/'evaluation-0/results.json').read_text())['trials']
        assert all(row['source_capture']['status']=='observed' for row in trials)
        old=scopes[-1];assert policy.implementation_prerequisites(old)['status'] == 'unknown'
        baseline=self.root/'foreign-baseline';baseline.mkdir()
        foreign=prerequisites._begin(baseline,'a'*64,time.monotonic()+5)
        try:assert policy.implementation_prerequisites(old)['status'] == 'unknown'
        finally:foreign.end()

    def test_actual_qualified_public_scope_satisfies_only_decide_isolation_element(self):
        spec=math_fixture.plan(n=1000,repeats=1)
        analysis=dict(math_fixture.analyze(spec,math_fixture.records(spec)),oracle_validity='current');decisions=[]
        def consumer(scope,read):
            packet=self.assert_observed(scope,read)
            with patch.object(policy,'implementation_prerequisites',side_effect=read):
                decisions.append(policy.decide(spec,analysis,prerequisite_scope=scope,isolation_verified=False))
            return packet
        result,_,_,_=self.run_public(consumer=consumer)
        assert result['implementation_prerequisites']['restricted_process_protocol']['status'] == 'qualified'
        assert [(row.status,row.reason,row.isolation,row.automatic) for row in decisions] == [('blocked','certification_prerequisites_unverified','qualified',False)]

    def test_missing_completion_cannot_disappear_from_admitted_coverage(self):
        result,_,_,_=self.run_public(mode='pending')
        packet=result['implementation_prerequisites'];assert packet['status'] == 'unknown'
        assert packet['coverage']['admitted'] > packet['coverage']['completed']
        assert all(value['status']=='unknown' for value in packet['checks'].values())

    def test_exception_cleanup_releases_owned_ticket_observation_and_fact_registries(self):
        gc.collect();before=(len(owned._DISPATCHES),len(owned._TICKETS),len(owned._FACTS),len(permission_audit._CUSTODY),len(prerequisites._STATES))
        result,_,_,_=self.run_public(mode='exception')
        assert result['implementation_prerequisites']['status'] == 'unknown'
        gc.collect()
        assert (len(owned._DISPATCHES),len(owned._TICKETS),len(owned._FACTS),len(permission_audit._CUSTODY),len(prerequisites._STATES)) == before

    def test_mutated_live_scope_cannot_be_resealed_by_report_metadata(self):
        def changed(scope,read):
            self.assert_observed(scope,read)
            scope.nonce='0'*64
            return read(scope)
        result,_,_,_=self.run_public(consumer=changed)
        assert result['implementation_prerequisites']['status'] == 'unknown'

    @pytest.mark.parametrize('field',('candidate','manifest'))
    def test_changed_candidate_and_manifest_invalidate_live_input_binding(self,field):
        def changed(scope,read):
            self.assert_observed(scope,read)
            path=Path(scope.data['bindings'][-1][field])
            path=path/'AGENTS.md' if field=='candidate' else path
            path.write_bytes(path.read_bytes()+b'\nSYNTHETIC_STALE_BINDING')
            return read(scope)
        result,_,_,_=self.run_public(consumer=changed)
        assert result['implementation_prerequisites']['status'] == 'unknown'

    def test_expired_scope_cannot_qualify_completed_work(self):
        def expired(scope,read):
            self.assert_observed(scope,read)
            with patch.object(prerequisites.time,'monotonic',return_value=scope.deadline+1):return read(scope)
        result,_,_,_=self.run_public(consumer=expired)
        assert result['implementation_prerequisites']['status'] == 'unknown'

    def test_unrequested_permission_profile_remains_unrequested_with_owned_direct_execution(self):
        result,_,_,_=self.run_public(audited=False)
        packet=result['implementation_prerequisites'];assert packet['status'] == 'observed', packet
        assert packet['permission_result_capture'] == 'unrequested'
        assert all(row['status']=='verified' for row in packet['checks'].values())

