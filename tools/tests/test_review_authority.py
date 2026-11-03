"""Synthetic key approvals are actor evidence, never certification authority."""
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch

import agent_harness_lab
from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.task_bank import validate
from agent_harness_lab.evaluation.bank_revisions import validate_admission
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.evaluation import review_authority
from agent_harness_lab.private_journal import encoded
if __package__:
    from . import test_regression_review as fixtures
else:
    import test_regression_review as fixtures


@unittest.skipUnless(Path('/usr/bin/ssh-keygen').is_file(),'optional OpenSSH verifier unavailable')
class ReviewAuthorityTests(fixtures.ReviewFixture):
    def test_optional_host_profile_and_request_limits_fail_without_events(self):
        before=self.store.history(self.case)
        with patch.object(review_authority.os,'geteuid',None):
            with self.assertRaises(review_authority.VerifierUnavailable):
                self.store.install_review_policy(self.policy)
        self.trust()
        with self.assertRaisesRegex(ValueError,'capacity'):
            review_authority.request(self.store,{'oversized':'x'*(64*1024)})
        with self.assertRaisesRegex(ValueError,'budget'):
            self.store.review(self.case_id,self.review_file(),budget_seconds=10**400)
        self.assertEqual(self.store.history(self.case),before)

    def setUp(self):
        super().setUp()
        expected=os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
        if expected:self.assertTrue(Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve()))
        self.drafted()
        self.operator=self.root/'operator';self.operator.mkdir(mode=0o700)
        self.key=self.operator/'reviewer'
        subprocess.run(['/usr/bin/ssh-keygen','-q','-t','ed25519','-N','','-C','synthetic','-f',str(self.key)],check=True,
            stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=5)
        key=' '.join(self.key.with_suffix('.pub').read_text().split()[:2])
        self.policy=self.operator/'policy.json'
        self.policy.write_text(json.dumps(dict(schema_version=1,profile='review-authority-policy-v1',
            purpose='development_artifact_review',reviewers={'synthetic-reviewer':key})))
        self.policy.chmod(0o600)

    def trust(self):
        with redirect_stdout(io.StringIO()):
            try:code=main(['regression-review','--store',str(self.store.root),'trust-reviewers','--policy',str(self.policy)])
            except SystemExit as error:code=error.code
        self.assertEqual(code,0,'public controller must support explicitly installed reviewer authority')

    def sign(self,path,*,key=None,namespace='ahl.artifact-review.v1'):
        signature=Path(str(path)+'.sig')
        signature.unlink(missing_ok=True)
        subprocess.run(['/usr/bin/ssh-keygen','-Y','sign','-f',str(key or self.key),'-n',namespace,str(path)],
            stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True,timeout=5)
        return signature

    def signed(self,**review_overrides):
        self.trust()
        prepared=self.store.prepare_review(self.case_id,self.review_file(**review_overrides))
        path=self.store.root/'review-requests'/(prepared['request_sha256']+'.json')
        return path,self.sign(path)

    def events(self):
        return {p.name:p.read_bytes() for p in (self.case/'events').glob('*.json')}

    def rejected(self,path,signature,reason):
        before=self.events()
        with self.assertRaisesRegex(ValueError,reason):
            self.store.review(self.case_id,path,signature_path=signature)
        self.assertEqual(self.events(),before)
        self.assertEqual(self.store.show(self.case_id)['state'],'drafted')

    def admit_signed_review(self):
        path,signature=self.signed()
        result=self.store.review(self.case_id,path,signature_path=signature)
        self.assertEqual(result['state'],'reviewed')
        self.assertEqual(result['review_authentication']['status'],'authenticated_key_approval')
        self.assertEqual(result['independence'],'unverified')
        parent=self.root/'parent'
        original=Path(agent_harness_lab.__file__).parent/'evaluation/fixtures/starter-v1'
        shutil.copytree(original,parent)
        bank=json.loads((parent/'bank.json').read_text())
        bank['tasks']=[task for task in bank['tasks'] if task['task_id']=='resumption']
        (parent/'bank.json').write_text(json.dumps(bank))
        request=dict(schema_version=1,case_id=self.case_id,expected_event_sha256=result['event_sha256'],
            evidence_sha256=result['evidence_sha256'],parent_bank_sha256=hashlib.sha256((parent/'bank.json').read_bytes()).hexdigest(),
            parent_revision_sha256=None,operator=dict(identity='synthetic-curator',source='operator_attestation',
                provenance='synthetic controller action',basis='Admit the reviewed regression.'),
            purpose='development_regression',provenance_binding=None,reason='Synthetic admission.')
        admission=self.root/'admit.json';admission.write_text(json.dumps(request))
        admitted=self.store.accept(self.case_id,parent/'bank.json',admission)
        self.assertTrue(admitted['current_eligible'])
        actual=self.store.root/'revisions'/admitted['revision_sha256']/'bank.json'
        self.assertFalse(validate(actual)['certification_eligible'])
        return actual

    def test_real_signed_review_admits_only_a_private_development_revision(self):
        actual=self.admit_signed_review()
        before=self.events();self.policy.unlink()
        self.assertFalse(validate_admission(actual)['current_eligible'])
        self.assertFalse(self.store.show(self.case_id)['bank_admission'])
        self.assertEqual(before,self.events())

    def test_stale_signed_retry_keeps_parent_verification_deadline(self):
        path,signature=self.signed()
        self.store.review(self.case_id,path,signature_path=signature)
        deadlines=[];verify=review_authority.verify;before=self.events()
        def track(*args,**kwargs):
            deadlines.append(kwargs.get('deadline'))
            return verify(*args,**kwargs)
        with patch.object(review_authority,'verify',side_effect=track):
            with self.assertRaisesRegex(ValueError,'stale'):
                self.store.review(self.case_id,path,signature_path=signature,budget_seconds=5)
        self.assertTrue(deadlines)
        self.assertTrue(all(value is not None for value in deadlines))
        self.assertEqual(len(set(deadlines)),1)
        self.assertEqual(before,self.events())

    def test_accepted_history_invalid_state_keeps_one_nested_deadline(self):
        self.admit_signed_review()
        path=self.review_file();deadlines=[];verify=review_authority.verify;before=self.events()
        def track(*args,**kwargs):
            deadlines.append(kwargs.get('deadline'))
            return verify(*args,**kwargs)
        with patch.object(review_authority,'verify',side_effect=track):
            with self.assertRaisesRegex(ValueError,'drafted case'):
                self.store.review(self.case_id,path,budget_seconds=5)
        self.assertGreaterEqual(len(deadlines),3)
        self.assertTrue(all(value is not None for value in deadlines))
        self.assertEqual(len(set(deadlines)),1)
        self.assertEqual(before,self.events())

    def test_unsigned_review_keeps_original_summary_and_event_shape(self):
        result=self.store.review(self.case_id,self.review_file())
        self.assertNotIn('review_authentication',result)
        self.assertEqual(result['review_independence'],'operator_attested')
        self.assertEqual(set(self.store.history(self.case)[-1][0]['payload']),{'review'})

    def test_wrong_key_and_namespace_cannot_append_review(self):
        path,signature=self.signed()
        other=self.operator/'other'
        subprocess.run(['/usr/bin/ssh-keygen','-q','-t','ed25519','-N','','-C','synthetic','-f',str(other)],check=True,
            stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=5)
        self.rejected(path,self.sign(path,key=other),'signature')
        self.rejected(path,self.sign(path,namespace='synthetic.other-purpose'),'signature')

    def test_unregistered_principal_cannot_claim_registered_key_approval(self):
        review=json.loads(self.review_file().read_text())['reviewer']
        review['identity']='synthetic-unregistered'
        path,signature=self.signed(reviewer=review)
        self.rejected(path,signature,'not authorized')

    def test_signed_payload_and_purpose_are_exact(self):
        path,signature=self.signed()
        value=json.loads(path.read_text());value['review']['reason']='Different synthetic verdict.'
        path.write_bytes(encoded(value))
        self.rejected(path,signature,'signature')
        value['purpose']='synthetic_other_purpose';path.write_bytes(encoded(value))
        self.rejected(path,self.sign(path),'scope changed')

    def test_stale_event_and_evidence_are_rejected_even_with_valid_signatures(self):
        path,signature=self.signed()
        value=json.loads(path.read_text())
        for field in ('expected_event_sha256','evidence_sha256'):
            with self.subTest(field=field):
                stale=json.loads(json.dumps(value));stale['review'][field]='f'*64
                path.write_bytes(encoded(stale))
                self.rejected(path,self.sign(path),'stale')

    def test_signed_approval_cannot_replay_across_store_or_current_event(self):
        path,signature=self.signed()
        original=path.read_bytes();value=json.loads(original);value['store_sha256']='f'*64
        path.write_bytes(encoded(value));self.rejected(path,self.sign(path),'scope changed')
        path.write_bytes(original);signature=self.sign(path)
        self.store.review(self.case_id,path,signature_path=signature)
        before=self.events()
        with self.assertRaisesRegex(ValueError,'stale'):
            self.store.review(self.case_id,path,signature_path=signature)
        self.assertEqual(before,self.events())

    def test_policy_revocation_blocks_new_approval_and_retains_historical_verdict(self):
        path,signature=self.signed()
        original=self.policy.read_bytes();self.policy.write_bytes(original+b'\n')
        self.rejected(path,signature,'changed|revoked')
        self.policy.write_bytes(original)
        result=self.store.review(self.case_id,path,signature_path=signature)
        before=self.events();self.policy.unlink()
        historical=self.store.show(self.case_id)
        self.assertEqual(historical['state'],'reviewed')
        self.assertEqual(historical['event_sha256'],result['event_sha256'])
        self.assertEqual(historical['review_authentication']['status'],'policy_unavailable_or_revoked')
        self.assertEqual(before,self.events())

    def test_changed_frozen_controls_quarantine_despite_valid_key_approval(self):
        path,signature=self.signed();before=self.events()
        (self.attempt()/'controls-source/4/original.txt').write_text('synthetic changed control\n')
        with self.assertRaisesRegex(ValueError,'quarantined'):
            self.store.review(self.case_id,path,signature_path=signature)
        self.assertEqual(self.store.show(self.case_id)['state'],'quarantined')
        for name,raw in before.items():self.assertEqual((self.case/'events'/name).read_bytes(),raw)

    def test_missing_verifier_never_falls_back_to_unsigned_review(self):
        path,signature=self.signed();before=self.events()
        with patch.object(review_authority,'VERIFIER',self.operator/'unavailable'):
            with self.assertRaisesRegex(review_authority.VerifierUnavailable,'openssh_review_verifier_unavailable'):
                self.store.review(self.case_id,path,signature_path=signature)
        self.assertEqual(before,self.events())
        self.assertEqual(self.store.show(self.case_id)['state'],'drafted')

    def test_real_verification_and_readback_share_one_deadline(self):
        path,signature=self.signed();deadlines=[]
        verify=review_authority.verify
        def track(*args,**kwargs):
            deadlines.append(kwargs.get('deadline'))
            return verify(*args,**kwargs)
        with patch.object(review_authority,'verify',side_effect=track):
            result=self.store.review(self.case_id,path,signature_path=signature,budget_seconds=5)
        self.assertEqual(result['state'],'reviewed')
        self.assertEqual(len(deadlines),2)
        self.assertIsNotNone(deadlines[0])
        self.assertEqual(deadlines[0],deadlines[1])


class ReviewAuthorityExportTests(unittest.TestCase):
    def test_key_approval_metadata_cannot_be_relabelled_public(self):
        public=dict(provenance=dict(source_kind='synthetic',approved_publication=True,
            source_ref='synthetic fixture',derived_from_private=False))
        self.assertTrue(public_export_allowed([public]))
        claims=[dict(profile=value) for value in (review_authority.PROFILE,review_authority.POLICY)]
        claims.extend(dict.fromkeys([name]) for name in ('review_authentication','authentication'))
        for claim in claims:
            for wrapper in (None,'receipt','analysis','context','binding'):
                with self.subTest(claim=claim,wrapper=wrapper):
                    item=dict(public)
                    item.update(claim if wrapper is None else {wrapper:claim})
                    self.assertFalse(public_export_allowed([item]))
