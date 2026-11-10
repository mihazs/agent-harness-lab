"""Synthetic key approvals are actor evidence, never certification authority."""
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
from unittest.mock import patch

import pytest

import agent_harness_lab
from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.task_bank import validate
from agent_harness_lab.evaluation.bank_revisions import validate_admission
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.evaluation import review_authority
from agent_harness_lab.private_journal import encoded
from tests.integration.evaluation import test_regression_review as fixtures
from tests.support.unittest_cleanups import run_cleanups


needs_openssh = pytest.mark.skipif(not Path('/usr/bin/ssh-keygen').is_file(), reason='optional OpenSSH verifier unavailable')


class ReviewAuthorityFixture(fixtures.ReviewFixture):
    """A drafted review case with a synthetic OpenSSH reviewer key and an owner-only reviewer policy."""

    def setUp(self):
        super().setUp()
        expected=os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
        if expected:assert Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve())
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
        assert code == 0, 'public controller must support explicitly installed reviewer authority'

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
        with pytest.raises(ValueError, match=reason):
            self.store.review(self.case_id,path,signature_path=signature)
        assert self.events() == before
        assert self.store.show(self.case_id)['state'] == 'drafted'

    def admit_signed_review(self):
        path,signature=self.signed()
        result=self.store.review(self.case_id,path,signature_path=signature)
        assert result['state'] == 'reviewed'
        assert result['review_authentication']['status'] == 'authenticated_key_approval'
        assert result['independence'] == 'unverified'
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
        assert admitted['current_eligible']
        actual=self.store.root/'revisions'/admitted['revision_sha256']/'bank.json'
        assert not validate(actual)['certification_eligible']
        return actual


@pytest.fixture
def authority():
    value = ReviewAuthorityFixture(); value.setUp()
    yield value
    run_cleanups(value)


@needs_openssh
def test_optional_host_profile_and_request_limits_fail_without_events(authority):
    before=authority.store.history(authority.case)
    with patch.object(review_authority.os,'geteuid',None):
        with pytest.raises(review_authority.VerifierUnavailable):
            authority.store.install_review_policy(authority.policy)
    authority.trust()
    with pytest.raises(ValueError, match='capacity'):
        review_authority.request(authority.store,{'oversized':'x'*(64*1024)})
    with pytest.raises(ValueError, match='budget'):
        authority.store.review(authority.case_id,authority.review_file(),budget_seconds=10**400)
    assert authority.store.history(authority.case) == before


@needs_openssh
def test_real_signed_review_admits_only_a_private_development_revision(authority):
    actual=authority.admit_signed_review()
    before=authority.events();authority.policy.unlink()
    assert not validate_admission(actual)['current_eligible']
    assert not authority.store.show(authority.case_id)['bank_admission']
    assert before == authority.events()


@needs_openssh
def test_stale_signed_retry_keeps_parent_verification_deadline(authority):
    path,signature=authority.signed()
    authority.store.review(authority.case_id,path,signature_path=signature)
    deadlines=[];verify=review_authority.verify;before=authority.events()
    def track(*args,**kwargs):
        deadlines.append(kwargs.get('deadline'))
        return verify(*args,**kwargs)
    with patch.object(review_authority,'verify',side_effect=track):
        with pytest.raises(ValueError, match='stale'):
            authority.store.review(authority.case_id,path,signature_path=signature,budget_seconds=5)
    assert deadlines
    assert all(value is not None for value in deadlines)
    assert len(set(deadlines)) == 1
    assert before == authority.events()


@needs_openssh
def test_accepted_history_invalid_state_keeps_one_nested_deadline(authority):
    authority.admit_signed_review()
    path=authority.review_file();deadlines=[];verify=review_authority.verify;before=authority.events()
    def track(*args,**kwargs):
        deadlines.append(kwargs.get('deadline'))
        return verify(*args,**kwargs)
    with patch.object(review_authority,'verify',side_effect=track):
        with pytest.raises(ValueError, match='drafted case'):
            authority.store.review(authority.case_id,path,budget_seconds=5)
    assert len(deadlines) >= 3
    assert all(value is not None for value in deadlines)
    assert len(set(deadlines)) == 1
    assert before == authority.events()


@needs_openssh
def test_unsigned_review_keeps_original_summary_and_event_shape(authority):
    result=authority.store.review(authority.case_id,authority.review_file())
    assert 'review_authentication' not in result
    assert result['review_independence'] == 'operator_attested'
    assert set(authority.store.history(authority.case)[-1][0]['payload']) == {'review'}


@needs_openssh
def test_wrong_key_and_namespace_cannot_append_review(authority):
    path,signature=authority.signed()
    other=authority.operator/'other'
    subprocess.run(['/usr/bin/ssh-keygen','-q','-t','ed25519','-N','','-C','synthetic','-f',str(other)],check=True,
        stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=5)
    authority.rejected(path,authority.sign(path,key=other),'signature')
    authority.rejected(path,authority.sign(path,namespace='synthetic.other-purpose'),'signature')


@needs_openssh
def test_unregistered_principal_cannot_claim_registered_key_approval(authority):
    review=json.loads(authority.review_file().read_text())['reviewer']
    review['identity']='synthetic-unregistered'
    path,signature=authority.signed(reviewer=review)
    authority.rejected(path,signature,'not authorized')


@needs_openssh
def test_signed_payload_and_purpose_are_exact(authority):
    path,signature=authority.signed()
    value=json.loads(path.read_text());value['review']['reason']='Different synthetic verdict.'
    path.write_bytes(encoded(value))
    authority.rejected(path,signature,'signature')
    value['purpose']='synthetic_other_purpose';path.write_bytes(encoded(value))
    authority.rejected(path,authority.sign(path),'scope changed')


@needs_openssh
@pytest.mark.parametrize('field', [
    pytest.param('expected_event_sha256', id='expected_event_sha256'),
    pytest.param('evidence_sha256', id='evidence_sha256')])
def test_stale_event_and_evidence_are_rejected_even_with_valid_signatures(authority, field):
    path,signature=authority.signed()
    value=json.loads(path.read_text())
    stale=json.loads(json.dumps(value));stale['review'][field]='f'*64
    path.write_bytes(encoded(stale))
    authority.rejected(path,authority.sign(path),'stale')


@needs_openssh
def test_signed_approval_cannot_replay_across_store_or_current_event(authority):
    path,signature=authority.signed()
    original=path.read_bytes();value=json.loads(original);value['store_sha256']='f'*64
    path.write_bytes(encoded(value));authority.rejected(path,authority.sign(path),'scope changed')
    path.write_bytes(original);signature=authority.sign(path)
    authority.store.review(authority.case_id,path,signature_path=signature)
    before=authority.events()
    with pytest.raises(ValueError, match='stale'):
        authority.store.review(authority.case_id,path,signature_path=signature)
    assert before == authority.events()


@needs_openssh
def test_policy_revocation_blocks_new_approval_and_retains_historical_verdict(authority):
    path,signature=authority.signed()
    original=authority.policy.read_bytes();authority.policy.write_bytes(original+b'\n')
    authority.rejected(path,signature,'changed|revoked')
    authority.policy.write_bytes(original)
    result=authority.store.review(authority.case_id,path,signature_path=signature)
    before=authority.events();authority.policy.unlink()
    historical=authority.store.show(authority.case_id)
    assert historical['state'] == 'reviewed'
    assert historical['event_sha256'] == result['event_sha256']
    assert historical['review_authentication']['status'] == 'policy_unavailable_or_revoked'
    assert before == authority.events()


@needs_openssh
def test_changed_frozen_controls_quarantine_despite_valid_key_approval(authority):
    path,signature=authority.signed();before=authority.events()
    (authority.attempt()/'controls-source/4/original.txt').write_text('synthetic changed control\n')
    with pytest.raises(ValueError, match='quarantined'):
        authority.store.review(authority.case_id,path,signature_path=signature)
    assert authority.store.show(authority.case_id)['state'] == 'quarantined'
    for name,raw in before.items():assert (authority.case/'events'/name).read_bytes() == raw


@needs_openssh
def test_missing_verifier_never_falls_back_to_unsigned_review(authority):
    path,signature=authority.signed();before=authority.events()
    with patch.object(review_authority,'VERIFIER',authority.operator/'unavailable'):
        with pytest.raises(review_authority.VerifierUnavailable, match='openssh_review_verifier_unavailable'):
            authority.store.review(authority.case_id,path,signature_path=signature)
    assert before == authority.events()
    assert authority.store.show(authority.case_id)['state'] == 'drafted'


@needs_openssh
def test_real_verification_and_readback_share_one_deadline(authority):
    path,signature=authority.signed();deadlines=[]
    verify=review_authority.verify
    def track(*args,**kwargs):
        deadlines.append(kwargs.get('deadline'))
        return verify(*args,**kwargs)
    with patch.object(review_authority,'verify',side_effect=track):
        result=authority.store.review(authority.case_id,path,signature_path=signature,budget_seconds=5)
    assert result['state'] == 'reviewed'
    assert len(deadlines) == 2
    assert deadlines[0] is not None
    assert deadlines[0] == deadlines[1]


CLAIMS = [pytest.param(dict(profile=review_authority.PROFILE), id='profile'),
          pytest.param(dict(profile=review_authority.POLICY), id='policy'),
          *(pytest.param(dict.fromkeys([name]), id=name) for name in ('review_authentication','authentication'))]


@pytest.mark.parametrize('wrapper', [pytest.param(wrapper, id=wrapper or 'bare')
                                     for wrapper in (None,'receipt','analysis','context','binding')])
@pytest.mark.parametrize('claim', CLAIMS)
def test_key_approval_metadata_cannot_be_relabelled_public(claim, wrapper):
    public=dict(provenance=dict(source_kind='synthetic',approved_publication=True,
        source_ref='synthetic fixture',derived_from_private=False))
    assert public_export_allowed([public])
    item=dict(public)
    item.update(claim if wrapper is None else {wrapper:claim})
    assert not public_export_allowed([item])
