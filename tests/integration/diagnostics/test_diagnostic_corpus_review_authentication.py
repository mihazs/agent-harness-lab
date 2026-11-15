"""Synthetic OpenSSH key approvals of corpus reviews; a verified key is not an independent human or a gold label."""
import json
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from agent_harness_lab.diagnostics.corpus import CorpusStore
from agent_harness_lab.evaluation import review_authority
from tests.integration.diagnostics.test_diagnostic_corpus import label, sample

pytestmark = pytest.mark.skipif(not Path('/usr/bin/ssh-keygen').is_file(), reason='optional OpenSSH verifier unavailable')
NAMESPACE = 'ahl.diagnostic-corpus-review.v1'
ONE, TWO, JUDGE = 'synthetic-reviewer-one', 'synthetic-reviewer-two', 'synthetic-adjudicator'


def keygen(path):
    subprocess.run(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'synthetic', '-f', str(path)], check=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    return ' '.join(path.with_suffix('.pub').read_text().split()[:2])


class Approvals:
    """A corpus store with an installed reviewer policy and one synthetic key per principal."""

    def __init__(self, tmp_path, shared_key=False):
        self.store = CorpusStore(tmp_path/'corpus')
        self.operator = tmp_path/'operator'; self.operator.mkdir(mode=0o700)
        self.keys = {name: self.operator/name for name in (ONE, TWO, JUDGE)}
        public = {name: keygen(path) for name, path in self.keys.items()}
        if shared_key: public[TWO] = public[ONE]; self.keys[TWO] = self.keys[ONE]
        self.policy = self.operator/'policy.json'
        self.write_policy(public)

    def write_policy(self, reviewers, purpose='diagnostic_corpus_review'):
        self.policy.write_text(json.dumps(dict(schema_version=1, profile='review-authority-policy-v1', purpose=purpose, reviewers=reviewers)))
        self.policy.chmod(0o600)

    def trust(self):
        return self.store.install_review_policy(self.policy)

    def request_for(self, item, kind, identity, value, reason=None):
        return self.store.prepare_review(item['case_id'], item['event_sha256'], kind, identity, value, reason)

    def sign(self, prepared, key=None, namespace=NAMESPACE):
        path = Path(prepared['request_path']); signature = Path(str(path)+'.sig'); signature.unlink(missing_ok=True)
        subprocess.run(['/usr/bin/ssh-keygen', '-Y', 'sign', '-f', str(key), '-n', namespace, str(path)], check=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
        return signature.read_text()

    def approve(self, item, kind, identity, value, reason=None):
        prepared = self.request_for(item, kind, identity, value, reason)
        return self.store.submit_authenticated(prepared['request'], self.sign(prepared, self.keys[identity]))

    def claim(self, item, identity):
        return self.store.update(item['case_id'], item['event_sha256'], 'claimed', reviewer=identity)

    def review(self, item, signed=(True, True, True), final=None):
        """Two reviews and an adjudication; each approval is signed or an unsigned operator declaration."""
        value = label(); final = final or value
        for identity, authenticated in ((ONE, signed[0]), (TWO, signed[1])):
            item = self.claim(item, identity)
            item = (self.approve(item, 'submitted', identity, value) if authenticated else
                self.store.update(item['case_id'], item['event_sha256'], 'submitted', reviewer=identity, label=value))
        if signed[2]: return self.approve(item, 'adjudicated', JUDGE, final, 'Synthetic adjudication basis')
        return self.store.update(item['case_id'], item['event_sha256'], 'adjudicated', reviewer=JUDGE, label=final, reason='Synthetic adjudication basis')

    def events(self, item):
        return {p.name: p.read_bytes() for p in (self.store.case(item['case_id'])/'events').glob('*.json')}


@pytest.fixture
def approvals(tmp_path):
    found = Approvals(tmp_path); found.trust()
    return found


def verdict(approvals, item):
    report = approvals.store.gold_label_policy()
    return next(row for row in report['cases'] if row['case_id'] == item['case_id'])


def test_signed_submission_is_recorded_with_its_key_approval_and_advances_the_queue(approvals):
    item = approvals.claim(approvals.store.enqueue(sample()), ONE)
    result = approvals.approve(item, 'submitted', ONE, label())
    event, _ = approvals.store.history(approvals.store.case(item['case_id']))[-1]
    assert result['state'] == 'submitted' and event['kind'] == 'submitted'
    assert event['payload']['authentication']['profile'] == 'authenticated-artifact-review-v1'
    assert (approvals.store.root/'review-auth'/(event['payload']['authentication']['record_sha256']+'.json')).is_file()
    assert result['gold_label'] is False and result['reviewer_independence'] == 'unverified'


def test_three_distinct_authenticated_approvals_meet_the_policy_without_issuing_a_gold_label(approvals):
    item = approvals.review(approvals.store.enqueue(sample()))
    report = approvals.store.gold_label_policy()
    row, = report['cases']
    assert (row['meets_policy'], row['blockers']) == (True, [])
    assert report['meeting_policy'] == 1 and report['gold_labels_issued'] == 0
    assert report['reviewer_independence'] == 'unverified' and report['root_independence'] == 'unverified'
    assert report['classifier_selection_allowed'] is False and report['publication_allowed'] is False
    assert item['gold_label'] is False and approvals.store.export_development(report['revision_sha256'])['records'][0]['gold_label'] is False


@pytest.mark.parametrize('signed', [(False, True, True), (True, False, True), (True, True, False), (False, False, False)],
    ids=['first-unsigned', 'second-unsigned', 'adjudication-unsigned', 'all-unsigned'])
def test_an_unsigned_review_event_blocks_the_policy_even_when_the_queue_accepts_it(approvals, signed):
    item = approvals.review(approvals.store.enqueue(sample()), signed)
    assert item['state'] == 'adjudicated'
    assert verdict(approvals, item) == dict(case_id=item['case_id'], state='adjudicated', meets_policy=False, blockers=['unauthenticated_review'])


def test_a_pending_case_is_not_adjudicated_and_never_meets_the_policy(approvals):
    item = approvals.store.enqueue(sample())
    assert verdict(approvals, item)['blockers'] == ['not_adjudicated']
    assert verdict(approvals, item)['meets_policy'] is False


def test_an_unknown_adjudication_stays_unresolved_even_with_three_approvals(approvals):
    unknown = label('unknown', 'unknown'); unknown.update(verification='unknown', evidence_refs=[], symptoms=[])
    item = approvals.review(approvals.store.enqueue(sample()), final=unknown)
    assert verdict(approvals, item)['blockers'] == ['unknown_outcome']


def test_two_identities_backed_by_one_key_do_not_meet_the_policy(tmp_path):
    shared = Approvals(tmp_path, shared_key=True); shared.trust()
    item = shared.review(shared.store.enqueue(sample()))
    assert verdict(shared, item)['blockers'] == ['reused_reviewer_key']


def test_a_label_changed_after_signing_is_rejected_and_records_nothing(approvals):
    item = approvals.claim(approvals.store.enqueue(sample()), ONE)
    prepared = approvals.request_for(item, 'submitted', ONE, label())
    signature = approvals.sign(prepared, approvals.keys[ONE])
    forged = json.loads(json.dumps(prepared['request'])); forged['review']['label'] = label('successful', 'recovered')
    before = approvals.events(item)
    with pytest.raises(ValueError, match='signature is invalid'):
        approvals.store.submit_authenticated(forged, signature)
    assert approvals.events(item) == before


@pytest.mark.parametrize('how', ['other-key', 'other-namespace'])
def test_a_signature_from_an_unauthorized_key_or_namespace_records_nothing(approvals, tmp_path, how):
    item = approvals.claim(approvals.store.enqueue(sample()), ONE)
    prepared = approvals.request_for(item, 'submitted', ONE, label())
    stranger = tmp_path/'stranger'; keygen(stranger)
    signature = (approvals.sign(prepared, stranger) if how == 'other-key' else
        approvals.sign(prepared, approvals.keys[ONE], namespace='ahl.artifact-review.v1'))
    before = approvals.events(item)
    with pytest.raises(ValueError, match='signature is invalid'):
        approvals.store.submit_authenticated(prepared['request'], signature)
    assert approvals.events(item) == before


def test_a_signed_request_goes_stale_when_the_case_moves_on(approvals):
    item = approvals.claim(approvals.store.enqueue(sample()), ONE)
    prepared = approvals.request_for(item, 'submitted', ONE, label())
    signature = approvals.sign(prepared, approvals.keys[ONE])
    approvals.store.update(item['case_id'], item['event_sha256'], 'submitted', reviewer=ONE, label=label())
    with pytest.raises(ValueError, match='stale corpus event revision'):
        approvals.store.submit_authenticated(prepared['request'], signature)


def test_a_signed_request_cannot_be_replayed_against_another_store(approvals, tmp_path):
    item = approvals.claim(approvals.store.enqueue(sample()), ONE)
    prepared = approvals.request_for(item, 'submitted', ONE, label())
    signature = approvals.sign(prepared, approvals.keys[ONE])
    other = CorpusStore(tmp_path/'other'); other.install_review_policy(approvals.policy)
    with pytest.raises(ValueError, match='scope changed'):
        other.submit_authenticated(prepared['request'], signature)


def test_preparing_a_review_requires_an_installed_policy(tmp_path):
    store = CorpusStore(tmp_path/'corpus'); item = store.enqueue(sample()); store.update(item['case_id'], item['event_sha256'], 'claimed', reviewer=ONE)
    item = store.snapshot()['items'][0]
    with pytest.raises(ValueError):
        store.prepare_review(item['case_id'], item['event_sha256'], 'submitted', ONE, label())


def test_a_policy_for_another_purpose_is_not_a_corpus_reviewer_policy(tmp_path):
    other = Approvals(tmp_path); other.write_policy(json.loads(other.policy.read_text())['reviewers'], purpose='development_artifact_review')
    with pytest.raises(ValueError, match='unsupported review authority policy'):
        other.trust()


@pytest.mark.parametrize(('kind', 'reason'), [('claimed', None), ('exposed', 'Synthetic exposure'), ('submitted', 'A stray reason'),
    ('adjudicated', None)], ids=['claim', 'exposure', 'submission-with-reason', 'adjudication-without-reason'])
def test_only_reasoned_submissions_and_adjudications_can_be_prepared(approvals, kind, reason):
    item = approvals.claim(approvals.store.enqueue(sample()), ONE)
    with pytest.raises(ValueError):
        approvals.request_for(item, kind, ONE, label(), reason)
    assert not (approvals.store.root/'review-requests').exists()


def test_a_revoked_policy_blocks_the_gold_label_policy_but_keeps_the_review_history_readable(approvals, tmp_path):
    item = approvals.review(approvals.store.enqueue(sample()))
    approvals.write_policy({ONE: keygen(tmp_path/'replacement')})
    assert verdict(approvals, item)['blockers'] == ['review_signature_invalid_or_revoked']
    assert approvals.store.snapshot()['items'][0]['state'] == 'adjudicated'


def test_a_missing_verifier_leaves_the_policy_unknown_not_failed(approvals):
    item = approvals.review(approvals.store.enqueue(sample()))
    with patch.object(review_authority, 'VERIFIER', Path('/nonexistent/ssh-keygen')):
        row = verdict(approvals, item)
        report = approvals.store.gold_label_policy()
    assert (row['meets_policy'], row['blockers']) == (None, ['verification_unavailable'])
    assert report['meeting_policy'] == 0 and report['unverifiable'] == 1


def test_a_stored_approval_edited_after_recording_makes_the_queue_fail_closed(approvals):
    item = approvals.review(approvals.store.enqueue(sample()))
    stored = next((approvals.store.root/'review-auth').iterdir())
    value = json.loads(stored.read_text()); value['request']['review']['label']['reason'] = 'Edited after signing'
    stored.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='authenticated review'):
        approvals.store.snapshot()
    assert item['state'] == 'adjudicated'


def test_a_binding_swapped_to_another_cases_valid_approval_makes_the_queue_fail_closed(approvals):
    first = approvals.claim(approvals.store.enqueue(sample('synthetic-first')), ONE)
    second = approvals.claim(approvals.store.enqueue(sample('synthetic-second')), ONE)
    approvals.approve(first, 'submitted', ONE, label()); approvals.approve(second, 'submitted', ONE, label())
    mine, theirs = (approvals.store.case(item['case_id'])/'events'/'0002.json' for item in (first, second))
    event = json.loads(mine.read_text()); event['payload']['authentication'] = json.loads(theirs.read_text())['payload']['authentication']
    mine.write_text(json.dumps(event))
    with pytest.raises(ValueError, match='differs from its exact case event'):
        approvals.store.snapshot()
