"""Real synthetic SSH approvals record intent; they never issue application grants."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from agent_harness_lab import conditional_application as application, config
from agent_harness_lab.cli import main
from agent_harness_lab.evaluation import cgroup, review_authority as authority
from agent_harness_lab.evaluation.regression_review import ReviewStore
from agent_harness_lab.private_journal import encoded, record


@unittest.skipUnless(Path('/usr/bin/ssh-keygen').is_file(), 'optional OpenSSH verifier unavailable')
class ConditionalApplicationAuthorityTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.root=Path(temporary.name)
        self.project=self.root/'project';self.project.mkdir();(self.project/'AGENTS.md').write_text('Synthetic baseline\n')
        self.store=ReviewStore(self.root/'review')
        allocated=self.invoke('allocate', str(self.project));self.authorization=allocated['authorization_id']
        operator=self.root/'operator';operator.mkdir(mode=0o700);self.key=operator/'reviewer'
        subprocess.run(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'synthetic', '-f', str(self.key)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=5)
        public=' '.join(self.key.with_suffix('.pub').read_text().split()[:2])
        self.policy=operator/'application-policy.json'
        self.policy.write_bytes(encoded(dict(schema_version=1, profile=authority.POLICY, purpose=application.PURPOSE,
            reviewers={'synthetic-reviewer':public})));self.policy.chmod(0o600)
        self.invoke('install-policy', str(self.policy))
        image=self.root/'image';image.mkdir(mode=0o700)
        self.delegation=self.root/'delegation';self.delegation.mkdir()
        credential=self.root/'unused-synthetic-token';credential.write_text('SYNTHETIC_UNUSED');credential.chmod(0o600)
        self.inference=self.root/'settings.json'
        self.inference.write_bytes(encoded(dict(schema_version=1, model='synthetic', credential_file=str(credential),
            max_requests=3, max_tool_calls=3, max_output_tokens=100, max_request_bytes=32768, max_response_bytes=16384,
            instruction_protocol='ahl-controlled-agents-v1')))
        delegated=self.store.root/'delegations'/('c'*64);delegated.mkdir(parents=True, mode=0o700)
        request=delegated/'request.json';request.write_text('{}\n')
        signature=Path(str(request)+'.sig');signature.write_text('SYNTHETIC_UNVERIFIED_DELEGATION\n')
        settings=config.prepare(dict(schema_version=2, profile='cooperative', recommendation_only=False, harness_paths=['AGENTS.md'],
            runtime=dict(name='responses', inference_settings=str(self.inference), cgroup_root=str(self.delegation),
                image_root=str(image), image_sha256='d'*64, disk_bytes=64*1024*1024, permission_audit='permission-results-v1'),
            limits=dict(seconds=150, trials=24), application=dict(policy=application.POLICY, review_store=str(self.store.root),
                authorization_id=self.authorization, bank_case_id='b'*64, delegation_request=str(request), delegation_signature=str(signature),
                method=dict(experiment_id='synthetic-preauthorization', repeats=1, seed=7, max_trials=4, min_roots=2,
                    effect_margin=.01, family_margin=.01, alpha=.99, family_weights=dict(synthetic=1.), precision_half_width=None))))
        config.create(self.project, settings)
        self.enterContext(patch.object(cgroup, 'delegated', return_value=self.delegation))
        prepared=self.invoke('prepare', self.authorization, str(self.project), '--reviewer', 'synthetic-reviewer')
        self.request=self.store.root/'review-requests'/(prepared['request_sha256']+'.json')

    def invoke(self, *arguments):
        output=io.StringIO()
        with redirect_stdout(output):code=main(['application-review', '--store', str(self.store.root), *arguments])
        self.assertEqual(code, 0)
        return json.loads(output.getvalue())

    def sign(self, namespace='ahl.instruction-application.v1'):
        signature=Path(str(self.request)+'.sig');signature.unlink(missing_ok=True)
        subprocess.run(['/usr/bin/ssh-keygen', '-Y', 'sign', '-f', str(self.key), '-n', namespace, str(self.request)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=5)
        return signature

    def events(self):
        return {path.name:path.read_bytes() for path in (self.store.case(self.authorization)/'events').iterdir()}

    def rejected(self, signature, reason):
        before=self.events()
        with self.assertRaisesRegex(ValueError, reason):
            application.authorize(self.store, self.authorization, self.request, signature, deadline=time.monotonic()+10)
        self.assertEqual(self.events(), before)
        self.assertEqual((self.project/'AGENTS.md').read_text(), 'Synthetic baseline\n')

    def test_real_installed_key_approves_exact_request_without_minting_a_live_grant(self):
        result=self.invoke('authorize', self.authorization, str(self.request), str(self.sign()))
        self.assertEqual(result['state'], 'application_authorized');self.assertFalse(result['automatic_promotion'])
        _,_,events=self.store.opened(self.authorization)
        authentication=events[-1][0]['payload']['authentication']
        saved,_=record(self.store.root/'review-auth'/(authentication['record_sha256']+'.json'),2*authority.MAX_BYTES)
        self.assertEqual(saved['proof']['namespace'], 'ahl.instruction-application.v1')
        self.assertEqual(saved['request']['purpose'], application.PURPOSE)
        self.assertEqual(saved['proof']['signer'], 'synthetic-reviewer')
        self.assertNotIn('Synthetic baseline', json.dumps(saved))
        for value in (result, saved['request'], {'authorization_id':self.authorization}, application._Session()):
            with self.subTest(kind=type(value).__name__):
                with self.assertRaises(ValueError):application._owned(value)
        with self.assertRaises(ValueError):application._bank_guard(application._Grant(), None)
        self.assertEqual((self.project/'AGENTS.md').read_text(), 'Synthetic baseline\n')

    def test_signature_for_another_namespace_cannot_append_an_approval(self):
        self.rejected(self.sign('ahl.artifact-review.v1'), 'signature is invalid')

    def test_changed_full_configuration_is_rejected_before_recording_approval(self):
        signature=self.sign();path=self.project/'ahl.json';value=json.loads(path.read_bytes())
        value['limits']['seconds']-=1;path.write_bytes(encoded(value))
        self.rejected(signature, 'configuration or references changed')

    def test_changed_runtime_settings_reference_is_rejected_before_recording_approval(self):
        signature=self.sign();value=json.loads(self.inference.read_bytes());value['model']='synthetic-changed'
        self.inference.write_bytes(encoded(value))
        self.rejected(signature, 'configuration or references changed')

    def test_revoked_installed_policy_cannot_approve_a_prepared_request(self):
        signature=self.sign();value=json.loads(self.policy.read_bytes())
        value['reviewers']={'different-reviewer':value['reviewers']['synthetic-reviewer']};self.policy.write_bytes(encoded(value))
        self.rejected(signature, 'changed or was revoked')

    def test_retired_authorization_cannot_be_approved_or_resurrected(self):
        signature=self.sign();result=self.invoke('retire', self.authorization)
        self.assertEqual(result['state'], 'retired')
        self.rejected(signature, 'request is stale')

    def test_actual_timeout_and_trial_caps_cannot_exceed_the_signed_configuration(self):
        settings,pin=config.load(self.project/'ahl.json');options=config.execution_options(self.project,settings,{},set())
        for changes in (dict(timeout=151),dict(trial_limit=25)):
            with self.subTest(changes=changes),self.assertRaisesRegex(ValueError,'signed budget'):
                application.binding(self.project,settings,pin,{**options,**changes})
        lowered=application.binding(self.project,settings,pin,{**options,'timeout':75})
        self.assertEqual(lowered['limits'],settings['limits'])
        changed=json.loads(json.dumps(settings));changed['harness_paths']=[]
        with self.assertRaisesRegex(ValueError,'effective configuration'):
            application.binding(self.project,changed,pin,options)

    def test_unsupported_explicit_store_and_contract_route_stop_before_side_effects(self):
        from agent_harness_lab import one_call
        foreign=self.root/'foreign-history'
        for extra in (['--evaluation-store',str(foreign)],['--repair-contracts']):
            output=io.StringIO()
            with self.subTest(extra=extra),patch.dict(os.environ,{'XDG_STATE_HOME':str(self.root/'state')}), \
                    patch.object(one_call,'contracts',side_effect=AssertionError('unsupported contract route')),redirect_stdout(output):
                self.assertEqual(main(['improve',str(self.project),*extra]),3)
            self.assertEqual(json.loads(output.getvalue())['reason'],'conditional_application_requires_plain_controlled_improve')
            self.assertFalse(foreign.exists())


if __name__=='__main__':unittest.main()
