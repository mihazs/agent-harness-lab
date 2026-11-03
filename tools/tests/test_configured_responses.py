"""Configured Responses selection and local-only readiness, with synthetic refs."""
from argparse import Namespace
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_harness_lab import config,one_call,workflow
from agent_harness_lab.onboarding import doctor


class ConfiguredResponsesTests(unittest.TestCase):
    def setUp(self):
        import agent_harness_lab
        expected=os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
        if expected:self.assertTrue(Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve()))
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.project=self.root/'project';self.project.mkdir()
        self.image=self.root/'runtime';self.image.mkdir(mode=0o700)
        self.cgroup=self.root/'delegation';self.cgroup.mkdir()
        self.key=self.root/'synthetic-token';self.key.write_text('SYNTHETIC_UNUSED');self.key.chmod(0o600)
        self.settings=self.root/'responses.json'
        self.settings.write_text(json.dumps(dict(schema_version=1,model='synthetic',credential_file=str(self.key),
            max_requests=60,max_tool_calls=40,max_output_tokens=100,max_request_bytes=32768,max_response_bytes=16384,
            instruction_protocol='ahl-controlled-agents-v1')))

    def runtime(self):
        return dict(name='responses',inference_settings=str(self.settings),cgroup_root=str(self.cgroup),
            image_root=str(self.image),image_sha256='a'*64,disk_bytes=67108864)

    def configured(self):
        value=config.prepare(dict(profile='cooperative',runtime=self.runtime(),harness_paths=['AGENTS.md']))
        config.create(self.project,value);return value

    def args(self,**changes):
        return Namespace(**dict(dict(project=self.project,agent='auto',codex='codex',claude='claude',timeout=None,
            inference_settings=None,cgroup_root=None,image_root=None,image_sha256=None,disk_bytes=None,
            permission_audit=None,sandbox_graders=False),**changes))

    def test_additive_runtime_does_not_change_legacy_defaults_or_digest(self):
        old=config.defaults();self.assertEqual(old['runtime'],dict(name='none',executable=None))
        value=self.configured();self.assertNotIn('executable',value['runtime'])
        expected=hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False).encode()).hexdigest()
        self.assertEqual(config.load(self.project/'ahl.json'),(value,expected))

    def test_plain_resolution_uses_existing_refs_and_native_override_keeps_shared_cli_options(self):
        self.configured();args=self.args();one_call.resolve(args,set())
        self.assertEqual(args.agent,'responses');self.assertEqual(args.inference_settings,self.settings)
        args=self.args(agent='codex',sandbox_graders=True,image_root=self.image,image_sha256='b'*64,cgroup_root=self.cgroup,disk_bytes=65536)
        one_call.resolve(args,{'--agent','--sandbox-graders','--image-root','--image-sha256','--cgroup-root','--disk-bytes'})
        self.assertEqual(args.agent,'codex');self.assertIsNone(args.inference_settings)
        self.assertEqual(args.image_sha256,'b'*64)

    def test_doctor_reports_local_refs_without_auth_read_or_execution(self):
        self.configured()
        from agent_harness_lab.evaluation import cgroup,responses
        with patch.object(cgroup,'delegated',return_value=self.cgroup),patch.object(responses,'read_credential',side_effect=AssertionError('no token read')),patch('subprocess.Popen',side_effect=AssertionError('no process')):
            result=doctor(self.project)
        self.assertEqual(result['status'],'ready',result)
        self.assertEqual(result['readiness_scope'],'local_configuration_and_references_only')
        checks={row['check_id']:row for row in result['checks']}
        self.assertEqual(checks['authentication']['status'],'unknown')
        self.assertEqual(checks['controlled_execution']['status'],'not_checked')

    def test_offline_policy_is_not_overridden_by_explicit_responses(self):
        config.create(self.project,config.defaults())
        args=self.args(agent='responses',inference_settings=self.settings)
        one_call.resolve(args,{'--agent','--inference-settings'})
        self.assertEqual(one_call.preflight(args),'configured_profile_does_not_allow_cooperative_improvement')

    def test_strict_variant_rejects_executables_unknown_fields_and_unsafe_bounds(self):
        for changes in (dict(executable='/unused'),dict(extra=True),dict(inference_settings='../settings'),
                dict(image_sha256='wrong'),dict(disk_bytes=True),dict(disk_bytes=1),dict(permission_audit='unreviewed')):
            with self.subTest(fields=sorted(changes)),self.assertRaises(ValueError):
                config.prepare(dict(profile='cooperative',runtime={**self.runtime(),**changes}))

    def test_relative_configuration_refs_are_project_anchored_and_repeated_resolution_uses_original_input(self):
        runtime=dict(self.runtime(),inference_settings='settings.json',image_root='runtime')
        config.create(self.project,config.prepare(dict(profile='cooperative',runtime=runtime)))
        args=self.args()
        with patch('os.getcwd',return_value=str(self.root/'unrelated')):one_call.resolve(args,set())
        self.assertEqual(args.inference_settings,self.project/'settings.json')
        source=config.load(self.project/'ahl.json')[0];source['runtime']['inference_settings']='new-settings.json'
        (self.project/'ahl.json').write_text(json.dumps(source));one_call.resolve(args,set())
        self.assertEqual(args.inference_settings,self.project/'new-settings.json')
        self.assertEqual(args.agent,'responses')

    def test_explicit_refs_win_and_native_with_exclusive_settings_is_rejected(self):
        self.configured();override=self.root/'explicit-settings'
        args=self.args(inference_settings=override);one_call.resolve(args,{'--inference-settings'})
        self.assertEqual(args.inference_settings,override)
        with self.assertRaisesRegex(ValueError,'inference settings require'):
            one_call.resolve(self.args(agent='claude',inference_settings=override),{'--agent','--inference-settings'})
        args=self.args(agent='codex');one_call.resolve(args,set())
        self.assertEqual(args.agent,'codex');self.assertIsNone(args.inference_settings)

    def test_responses_preflight_does_not_search_for_a_native_agent(self):
        self.configured();args=self.args();one_call.resolve(args,set())
        from agent_harness_lab.evaluation import cgroup
        with patch.object(cgroup,'delegated',return_value=self.cgroup),patch.object(one_call.shutil,'which',side_effect=AssertionError('no native runtime lookup')):
            self.assertIsNone(one_call.preflight(args))

    def test_missing_or_unsafe_local_refs_stop_before_probe_key_read_or_transport(self):
        self.configured();from agent_harness_lab import cli
        from agent_harness_lab.evaluation import cgroup,responses
        for change in ('missing','permissions'):
            with self.subTest(change=change):
                if change=='missing':self.key.unlink()
                else:self.key.write_text('SYNTHETIC_UNUSED');self.key.chmod(0o644)
                with patch.object(cgroup,'delegated',return_value=self.cgroup),patch('subprocess.Popen',side_effect=AssertionError('no probe')), \
                        patch.object(responses,'read_credential',side_effect=AssertionError('no token read')),patch.object(responses,'request',side_effect=AssertionError('no transport')):
                    with self.assertRaises(SystemExit):cli.main(['improve',str(self.project)])

    def test_live_watch_reference_changes_require_restart_and_keep_fingerprint_stable(self):
        self.configured();args=self.args();one_call.resolve(args,set())
        from agent_harness_lab.evaluation import cgroup
        with patch.object(cgroup,'delegated',return_value=self.cgroup):
            first=workflow.watch_configuration(args)
            self.assertEqual(first,workflow.watch_configuration(args))
            self.key.write_text('SYNTHETIC_CHANGED')
            with self.assertRaisesRegex(ValueError,'references changed'):workflow.watch_configuration(args)

    def test_changed_credential_metadata_after_validated_response_cannot_retry_or_reach_worker(self):
        self.configured();args=self.args();one_call.resolve(args,set())
        from agent_harness_lab.evaluation import cgroup,responses
        import test_sandbox_inference as wire
        calls=[]
        def transport(*unused):
            calls.append(True);self.key.write_text('SYNTHETIC_CHANGED')
            return wire.reply([wire.call()])
        deadline=time.monotonic()+10
        (self.project/'AGENTS.md').write_text('synthetic')
        work=self.root/'work';work.mkdir()
        backend=SimpleNamespace(image=SimpleNamespace(sha256='a'*64),run=lambda *args,**kwargs:self.fail('changed reference reached worker'))
        with patch.object(cgroup,'delegated',return_value=self.cgroup):
            self.assertIsNone(one_call.preflight(args))
            settings=json.loads(self.settings.read_text())
            client=responses.Client(settings,None,deadline,transport=transport,record=lambda value:one_call.check_configuration(args))
            with self.assertRaisesRegex(ValueError,'references changed'):
                responses.run_role(client,backend,work,{'harness':self.project},'executor','policy','task',deadline)
            with self.assertRaises((ValueError,responses.InferenceError)):
                responses.run_role(client,backend,work,{'harness':self.project},'executor','policy','task',deadline)
        self.assertEqual(calls,[True]);self.assertEqual(client.requests,1);self.assertTrue(client.poisoned)

    def test_doctor_never_opens_credential_contents_even_for_explicit_probe_request(self):
        self.configured();from agent_harness_lab.evaluation import cgroup
        opened=os.open
        def no_key(path,*args,**kwargs):
            if isinstance(path,(str,Path)) and Path(path).name==self.key.name:self.fail('doctor opened credential contents')
            return opened(path,*args,**kwargs)
        with patch.object(cgroup,'delegated',return_value=self.cgroup),patch.object(config,'supported',return_value=True),patch('os.open',side_effect=no_key), \
                patch('subprocess.Popen',side_effect=AssertionError('no doctor process')):
            result=doctor(self.project,probe_runtime=True)
        self.assertEqual(result['status'],'ready',result);self.assertFalse(result['external_runtime_executed'])

    def test_local_reference_preflight_uses_the_same_parent_budget_before_execution(self):
        self.configured();args=self.args(timeout=1);one_call.resolve(args,{'--timeout'})
        from agent_harness_lab.evaluation import cgroup
        with patch.object(cgroup,'delegated',return_value=self.cgroup), \
                patch.object(workflow,'time',SimpleNamespace(monotonic=iter([0,2]).__next__)), \
                patch.object(workflow,'_improve',side_effect=AssertionError('expired preflight reached pipeline')):
            result=workflow._run_improvement(args)
        self.assertEqual(result['status'],'budget_exhausted')

    def test_reference_checkpoint_pin_stays_private_without_hashing_credential_contents(self):
        self.configured();args=self.args();one_call.resolve(args,set())
        from agent_harness_lab.evaluation import cgroup
        from agent_harness_lab.evaluation.benchmark import public_export_allowed
        import test_benchmark_contracts as fixture
        with patch.object(cgroup,'delegated',return_value=self.cgroup):
            metadata=config.response_references(self.project,vars(args))
        self.assertNotIn('SYNTHETIC_UNUSED',json.dumps(metadata))
        self.assertFalse(public_export_allowed([{**fixture.record(),'receipt':dict(configured_response_references_sha256='a'*64)}]))
