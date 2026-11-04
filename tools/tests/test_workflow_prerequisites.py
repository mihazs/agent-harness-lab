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
import tempfile
import time
import unittest
from unittest.mock import Mock,patch

from agent_harness_lab import cli,workflow
from agent_harness_lab.evaluation import controller_receipts as owned,permission_audit,responses
from agent_harness_lab.evaluation.permission_audit import PROFILE,Observation
from agent_harness_lab.promotion import policy,prerequisites
from agent_harness_lab.sources.controller import SourceSink
from agent_harness_lab.sources.schema import digest
import test_evaluation_foundation as math_fixture
import test_sandbox_kernel as kernel
import test_sandbox_construction as roles
import test_workflow as workflow_fixture


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
        self.assertEqual(packet['status'],'observed',json.dumps(evidence,sort_keys=True))
        return packet

    def run_public(self,*,mode='real',source=False,consumer=None,audited=True):
        fixture=workflow_fixture.WorkflowTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        case=self.root/('case-'+str(len(list(self.root.glob('case-*')))));case.mkdir()
        runtime=case/'runtime'
        if mode=='fake':runtime.mkdir();pin=dict(sha256='a'*64)
        else:pin=kernel.pinned_runtime(runtime)
        settings=dict(schema_version=1,model='synthetic-transport',credential_file=str(case/'nonexistent-credential'),
            max_requests=60,max_tool_calls=40,max_output_tokens=100,max_request_bytes=32768,max_response_bytes=16384)
        counter=[0];scopes=[];completions=[]
        def transport(credential,body,max_bytes,deadline):
            self.assertIsNone(credential)
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
                helper=roles.ControlledWorkflowTests();helper.root=case;helper.roles=[]
                backend=Mock(permission_audit=PROFILE);backend.permission_reports=[]
                backend.capability.return_value=dict(status='available')
                backend.permission_qualification.side_effect=lambda:dict(profile=PROFILE,qualification='qualified')
                def execute(*args,**options):
                    run=helper.execute(*args,**options)
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
                    with self.assertRaisesRegex(RuntimeError,'synthetic lost completion'):cli.main(argv)
                else:cli.main(argv)
        result=json.loads((output/'workflow.json').read_text())
        self.assertEqual((fixture.project/'AGENTS.md').read_text(),'bad')
        self.assertFalse(result['automatic_promotion'])
        self.assertIsNone(prerequisites.current())
        if mode!='exception':self.assertEqual(counter[0],16)
        return result,scopes,completions,output


class PolicyAuthorityTests(PublicCase,unittest.TestCase):
    def setUp(self):
        expected=os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
        if expected:self.assertTrue(Path(cli.__file__).resolve().is_relative_to(Path(expected).resolve()))
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.cgroup_root=self.root/'unused-delegation'

    def test_failure_diagnostics_preserve_pre_read_state_without_content(self):
        scope=prerequisites._begin(self.root,'a'*64,time.monotonic()+30)
        self.addCleanup(scope.end)
        scope._admit('executor','role')
        with self.assertRaises(self.failureException) as failure:
            self.assert_observed(scope,policy.implementation_prerequisites)
        message=str(failure.exception)
        self.assertIn('"admitted": 1',message)
        self.assertIn('"completed": 0',message)
        self.assertIn('"role": "executor"',message)
        self.assertIn('"not_lost": true',message)
        self.assertNotIn(str(self.root),message)
        self.assertNotIn(scope.nonce,message)
        scope.cancel()
        with self.assertRaises(self.failureException) as failure:
            self.assert_observed(scope,policy.implementation_prerequisites)
        message=str(failure.exception)
        self.assertIn('"first_failed_scope_predicate": "not_lost"',message)
        self.assertIn('"read_exception": "ValueError"',message)
        self.assertIn('"read_reason": "scope_unavailable"',message)

    def test_legacy_recommendation_inputs_are_declarations_without_implementation_authority(self):
        spec=math_fixture.plan(n=1000,repeats=1);analysis=math_fixture.analyze(spec,math_fixture.records(spec))
        decision=policy.decide(spec,dict(analysis,oracle_validity='current'),isolation_verified=True,
            effective_runtime_verified=True,graders_audited=True,lineage_reviewed=True,certification_reserved=True)
        self.assertEqual(decision.status,'blocked');self.assertFalse(decision.automatic)
        self.assertEqual(decision.declared_prerequisites,{'isolation_verified':dict(status='declared_unverified',value=True)})
        packet=policy.implementation_prerequisites(declarations={'isolation_verified':True})
        self.assertEqual(packet['declared_prerequisites']['isolation_verified']['status'],'declared_unverified')
        self.assertTrue(all(row['status']=='unknown' for row in packet['checks'].values()))

    def test_fully_labeled_fake_backend_cannot_issue_implementation_facts(self):
        result,_,_,_=self.run_public(mode='fake')
        self.assertEqual(result['status'],'blocked')
        self.assertEqual(result['reason'],'restricted_process_protocol_unqualified')
        self.assertNotIn('recommendation',result)
        self.assertEqual(result['isolation_qualification']['qualification'],'qualified')
        self.assertEqual(result['implementation_prerequisites']['status'],'unknown')
        self.assertTrue(all(row['status']=='unknown' for row in result['implementation_prerequisites']['checks'].values()))

    def test_constructor_failures_and_mutated_fields_cannot_leak_scope_context(self):
        baseline=self.root/'baseline';baseline.mkdir()
        for image in (None,{},'invalid'):
            with self.subTest(image=image),self.assertRaises(ValueError):
                prerequisites._begin(baseline,image,time.monotonic()+5)
            self.assertIsNone(prerequisites.current())
        with patch.object(prerequisites._Scope,'_save',side_effect=RuntimeError('synthetic seal failure')):
            with self.assertRaises(RuntimeError):prerequisites._begin(baseline,'a'*64,time.monotonic()+5)
        self.assertIsNone(prerequisites.current())
        scope=prerequisites._begin(baseline,'a'*64,time.monotonic()+5)
        scope.deadline=object()
        self.assertEqual(policy.implementation_prerequisites(scope)['status'],'unknown')
        scope.end()
        self.assertIsNone(prerequisites.current())

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

    def test_forged_isolation_boolean_has_no_effect_and_is_recorded_as_declared(self):
        spec,analysis=self.certification_inputs()
        others=dict(effective_runtime_verified=True,graders_audited=True,lineage_reviewed=True,certification_reserved=True)
        baseline=policy.decide(spec,analysis,**others)
        for forged in (True,1,'qualified',object()):
            with self.subTest(forged=type(forged).__name__):
                decision=policy.decide(spec,analysis,isolation_verified=forged,**others)
                self.assertEqual((decision.status,decision.reason,decision.isolation,decision.automatic),
                    (baseline.status,baseline.reason,'unknown',False))
                self.assertEqual(decision.status,'blocked')
                self.assertEqual(decision.declared_prerequisites['isolation_verified']['status'],'declared_unverified')
        self.assertEqual(baseline.declared_prerequisites,{})
        json.dumps(asdict(policy.decide(spec,analysis,isolation_verified=object(),**others)),allow_nan=False)

    def test_lost_oversized_copied_or_unqualified_scope_gives_unknown_isolation(self):
        spec,analysis=self.certification_inputs()
        others=dict(isolation_verified=True,effective_runtime_verified=True,graders_audited=True,
            lineage_reviewed=True,certification_reserved=True)
        def assert_unknown(scope):
            decision=policy.decide(spec,analysis,prerequisite_scope=scope,**others)
            self.assertEqual((decision.status,decision.isolation),('blocked','unknown'))
        oversized=self.synthetic_scope()
        oversized.data['synthetic_padding']='x'*prerequisites.MAX_BYTES;oversized._save()
        self.assertTrue(oversized.lost);self.assertIsNone(prerequisites._STATES[oversized][2])
        assert_unknown(oversized);oversized.end()
        lost=self.synthetic_scope();lost.cancel();assert_unknown(lost);lost.end()
        live=self.synthetic_scope()
        assert_unknown(copy(live));assert_unknown(policy.implementation_prerequisites(live))
        assert_unknown(None);live.end();assert_unknown(live)
        for options in (dict(boundary='unknown'),dict(permission='unknown'),dict(permission=None),dict(permission='violated')):
            with self.subTest(**{key:str(value) for key,value in options.items()}):
                scope=self.synthetic_scope(**options);assert_unknown(scope);scope.end()

    def test_qualified_scope_satisfies_only_the_isolation_element(self):
        spec,analysis=self.certification_inputs();scope=self.synthetic_scope()
        packet=policy.implementation_prerequisites(scope)
        self.assertEqual(policy.isolation_qualification(scope),'qualified')
        alone=policy.decide(spec,analysis,prerequisite_scope=scope)
        self.assertEqual((alone.status,alone.reason,alone.isolation),('blocked','certification_prerequisites_unverified','qualified'))
        others=('effective_runtime_verified','graders_audited','lineage_reviewed','certification_reserved')
        for missing in others:
            with self.subTest(missing=missing):
                decision=policy.decide(spec,analysis,prerequisite_scope=scope,**{name:name!=missing for name in others})
                self.assertEqual((decision.status,decision.isolation),('blocked','qualified'))
        for blocked in (dict(analysis,oracle_validity='unverified'),dict(analysis,status='incomplete')):
            self.assertEqual(policy.decide(spec,blocked,prerequisite_scope=scope,**{name:True for name in others}).status,
                'blocked' if blocked['status']!='incomplete' else 'incomplete')
        smoke=replace(spec,profile='descriptive_smoke')
        self.assertEqual(policy.decide(smoke,analysis,prerequisite_scope=scope,**{name:True for name in others}).status,'inconclusive')
        decision=policy.decide(spec,analysis,prerequisite_scope=scope,**{name:True for name in others})
        self.assertEqual((decision.status,decision.automatic),('recommended',False))
        self.assertTrue(all(row['status']=='unverified' for row in packet['mandatory_certification'].values()))
        self.assertFalse(packet['adoption_authority']);self.assertFalse(packet['automatic_promotion'])

    def test_known_private_profile_and_packet_cannot_be_relabelled_for_benchmark_export(self):
        from agent_harness_lab.evaluation.benchmark import public_export_allowed
        import test_benchmark_contracts as fixtures
        packet=dict(policy.implementation_prerequisites(),publication_allowed=True)
        self.assertFalse(public_export_allowed([{**fixtures.record(),'implementation_prerequisites':packet}]))
        self.assertFalse(public_export_allowed([{**fixtures.record(),'receipt':packet}]))


@unittest.skipUnless(kernel.ENABLED,'explicit delegated kernel profile required')
class PublicControllerPrerequisiteTests(PublicCase,kernel.KernelFixture):
    def test_runtime_boundary_qualifies_actual_four_role_protocol_without_certification(self):
        result,_,completions,output=self.run_public(source=True)
        packet=result['implementation_prerequisites'];boundary=packet['restricted_process_protocol']
        self.assertEqual(boundary['status'],'qualified',boundary)
        self.assertEqual(boundary['observations'],packet['coverage']['admitted'])
        self.assertEqual(set(boundary['roles']),{'proposer','evaluator_author','executor','grader'})
        self.assertEqual(len(completions),len(set(completions)))
        self.assertTrue(all(row['status']=='unverified' for row in packet['mandatory_certification'].values()))
        self.assertEqual(packet['native_identity'],'unverified');self.assertFalse(packet['adoption_authority'])
        trials=json.loads((output/'evaluation-0/results.json').read_text())['trials']
        self.assertTrue(all(row['source_capture']['status']=='observed' for row in trials))

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
        self.assertEqual(removed,[('completed',0)])
        self.assertEqual(result['implementation_prerequisites']['restricted_process_protocol']['status'],'unknown')
        self.assertEqual(result['status'],'blocked');self.assertNotIn('recommendation',result)

    def test_real_public_all_role_workflow_shares_one_native_completion_with_source_and_rejects_replay(self):
        def consumer(scope,read):
            packet=self.assert_observed(scope,read)
            self.assertEqual(read(copy(scope))['status'],'unknown')
            self.assertEqual(read(packet)['status'],'unknown')
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
                    self.fail('forked workflow authority check exceeded its bound')
                self.assertEqual(os.waitstatus_to_exitcode(status),0,'fork inherited workflow prerequisite authority')
                self.assert_observed(scope,read)
            return packet
        result,scopes,completions,output=self.run_public(source=True,consumer=consumer)
        packet=result['implementation_prerequisites'];self.assertEqual(result['status'],'inconclusive')
        self.assertEqual(packet['status'],'observed',packet)
        self.assertEqual(set(packet['coverage']['roles']),{'proposer','evaluator_author','executor','grader'})
        self.assertTrue(all(row['status']=='verified' for row in packet['checks'].values()),packet)
        self.assertEqual(packet['permission_result_capture'],'qualified')
        self.assertGreater(packet['coverage']['capability_probes'],0)
        self.assertTrue(all(value=='violated' for value in packet['capability_probe_permission_results']))
        self.assertEqual(len(completions),packet['coverage']['admitted']);self.assertEqual(len(set(completions)),len(completions))
        self.assertFalse(packet['adoption_authority'])
        self.assertTrue(all(row['status']=='unverified' for row in packet['mandatory_certification'].values()))
        trials=json.loads((output/'evaluation-0/results.json').read_text())['trials']
        self.assertTrue(all(row['source_capture']['status']=='observed' for row in trials))
        old=scopes[-1];self.assertEqual(policy.implementation_prerequisites(old)['status'],'unknown')
        baseline=self.root/'foreign-baseline';baseline.mkdir()
        foreign=prerequisites._begin(baseline,'a'*64,time.monotonic()+5)
        try:self.assertEqual(policy.implementation_prerequisites(old)['status'],'unknown')
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
        self.assertEqual(result['implementation_prerequisites']['restricted_process_protocol']['status'],'qualified')
        self.assertEqual([(row.status,row.reason,row.isolation,row.automatic) for row in decisions],
            [('blocked','certification_prerequisites_unverified','qualified',False)])

    def test_missing_completion_cannot_disappear_from_admitted_coverage(self):
        result,_,_,_=self.run_public(mode='pending')
        packet=result['implementation_prerequisites'];self.assertEqual(packet['status'],'unknown')
        self.assertGreater(packet['coverage']['admitted'],packet['coverage']['completed'])
        self.assertTrue(all(value['status']=='unknown' for value in packet['checks'].values()))

    def test_exception_cleanup_releases_owned_ticket_observation_and_fact_registries(self):
        gc.collect();before=(len(owned._DISPATCHES),len(owned._TICKETS),len(owned._FACTS),len(permission_audit._CUSTODY),len(prerequisites._STATES))
        result,_,_,_=self.run_public(mode='exception')
        self.assertEqual(result['implementation_prerequisites']['status'],'unknown')
        gc.collect()
        self.assertEqual((len(owned._DISPATCHES),len(owned._TICKETS),len(owned._FACTS),len(permission_audit._CUSTODY),len(prerequisites._STATES)),before)

    def test_mutated_live_scope_cannot_be_resealed_by_report_metadata(self):
        def changed(scope,read):
            self.assert_observed(scope,read)
            scope.nonce='0'*64
            return read(scope)
        result,_,_,_=self.run_public(consumer=changed)
        self.assertEqual(result['implementation_prerequisites']['status'],'unknown')

    def test_changed_candidate_and_manifest_invalidate_live_input_binding(self):
        for field in ('candidate','manifest'):
            def changed(scope,read):
                self.assert_observed(scope,read)
                path=Path(scope.data['bindings'][-1][field])
                path=path/'AGENTS.md' if field=='candidate' else path
                path.write_bytes(path.read_bytes()+b'\nSYNTHETIC_STALE_BINDING')
                return read(scope)
            with self.subTest(field=field):
                result,_,_,_=self.run_public(consumer=changed)
                self.assertEqual(result['implementation_prerequisites']['status'],'unknown')

    def test_expired_scope_cannot_qualify_completed_work(self):
        def expired(scope,read):
            self.assert_observed(scope,read)
            with patch.object(prerequisites.time,'monotonic',return_value=scope.deadline+1):return read(scope)
        result,_,_,_=self.run_public(consumer=expired)
        self.assertEqual(result['implementation_prerequisites']['status'],'unknown')

    def test_unrequested_permission_profile_remains_unrequested_with_owned_direct_execution(self):
        result,_,_,_=self.run_public(audited=False)
        packet=result['implementation_prerequisites'];self.assertEqual(packet['status'],'observed',packet)
        self.assertEqual(packet['permission_result_capture'],'unrequested')
        self.assertTrue(all(row['status']=='verified' for row in packet['checks'].values()))


if __name__=='__main__':unittest.main()
