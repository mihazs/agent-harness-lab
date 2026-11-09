"""Synthetic controller cases and operator attestations, not authenticated reviewers."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.regression_review import ReviewStore
from tests.integration.evaluation import test_regression_replay as fixtures
from tests.support.unittest_cleanups import run_cleanups


class ReviewFixture(unittest.TestCase):
    """A ReviewStore holding one reported synthetic replay case.

    test_interchange, test_regression_admission and test_review_authority instantiate this class and call setUp,
    its helpers and doCleanups, so it keeps that TestCase interface and holds no tests.
    """

    def setUp(self):
        self.fixture = fixtures.RegressionReplayTests(); self.fixture.setUp()
        self.addCleanup(run_cleanups, self.fixture)
        self.root, self.backend = self.fixture.root, self.fixture.backend
        self.store = ReviewStore(self.root/'private-store')
        self.initial = self.store.propose(self.fixture.source, self.fixture.draft, self.fixture.spec, 'synthetic-author')
        self.case_id = self.initial['case_id']; self.case = self.store.case(self.case_id)

    def reproduce(self, **kwargs):
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend', return_value=self.backend):
            return self.store.reproduce(self.case_id, self.backend, **kwargs)

    def qualified(self):
        result = self.reproduce(); assert result['state'] == 'reproduced', result
        return result

    def drafted(self):
        self.qualified(); return self.store.draft(self.case_id)

    def review_file(self, decision='reviewed', **overrides):
        current = self.store.show(self.case_id)
        value = dict(schema_version=1, case_id=self.case_id, expected_event_sha256=current['event_sha256'],
            evidence_sha256=current['evidence_sha256'], decision=decision,
            reviewer=dict(identity='synthetic-reviewer', source='operator_attestation', provenance='synthetic operator review record',
                          basis_kind='independent_artifact_review', basis='Separate inspection of this frozen artifact evidence.'),
            evidence_refs=[dict(ref='synthetic:review', sha256='a'*64)], reason='Synthetic review decision.')
        value.update(overrides)
        path = self.root/'operator-review.json'; path.write_text(json.dumps(value))
        return path

    def attempt(self):
        event = next(e for e, _ in reversed(self.store.history(self.case)) if e['kind'] == 'reproduced')
        return self.case/event['payload']['attempt']


@pytest.fixture
def review():
    value = ReviewFixture(); value.setUp()
    yield value
    run_cleanups(value)


def test_reported_reproduced_drafted_reviewed_chain_preserves_limits_and_original_inputs(review):
    original = {p:digest(review.root/p) for p in ('fixture','oracle','before','after','recorded')}
    assert review.initial['state'] == 'reported'; review.backend.run.assert_not_called()
    review.qualified(); review.store.draft(review.case_id)
    result = review.store.review(review.case_id, review.review_file())
    assert result['state'] == 'reviewed'
    assert result['review_independence'] == 'operator_attested'
    assert [e['kind'] for e, _ in review.store.history(review.case)] == ['reported','attempt_started','reproduced','drafted','reviewed']
    assert original == {p:digest(review.root/p) for p in original}
    for key in ('bank_admission', 'publication_allowed', 'automatic_promotion'): assert not result[key]
    assert result['historical_verification'] == 'unverified'; assert result['independence'] == 'unverified'
    assert review.store.root.stat().st_mode & 0o777 == 0o700
    for path in [review.case/'inputs.json', *list((review.case/'events').glob('*.json'))]:
        assert path.stat().st_mode & 0o777 == 0o600
    assert (review.case/'proposal.json').read_bytes() == review.fixture.source.read_bytes()


def test_source_declaration_change_quarantines_without_execution(review):
    review.fixture.draft.write_text(review.fixture.draft.read_text()+' ')
    with pytest.raises(ValueError, match='quarantined'): review.reproduce()
    assert review.store.show(review.case_id)['state'] == 'quarantined'
    review.backend.run.assert_not_called()


def test_caller_success_object_without_controller_artifacts_cannot_reproduce(review):
    with patch('agent_harness_lab.evaluation.regression_review.replay', return_value={'status':'replay_completed'}):
        result = review.reproduce()
    assert result['state'] == 'reported'; assert result['last_attempt_status'] == 'incomplete'
    assert result['evidence_sha256'] is None


@pytest.mark.skipif(not Path('/proc/self/fd').is_dir(), reason='requires Linux descriptor fault injection')
def test_critical_artifact_durability_failure_never_commits_reproduction(review):
    fsync = os.fsync
    def fail_data(descriptor):
        path = Path(os.readlink('/proc/self/fd/'+str(descriptor)))
        if 'controls-source' in path.parts: raise OSError('SYNTHETIC_DATA_FSYNC_FAILURE')
        return fsync(descriptor)
    with patch('os.fsync', side_effect=fail_data): result = review.reproduce()
    assert result['state'] == 'reported'; assert result['last_attempt_status'] == 'incomplete'
    assert result['evidence_sha256'] is None


def test_baseline_already_passing_never_advances_lifecycle(review):
    review.fixture.change_config('before', 'config', {'enabled':True})
    other = ReviewStore(review.root/'other-store')
    result = other.propose(review.fixture.source, review.fixture.draft, review.fixture.spec, 'synthetic-author')
    with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend', return_value=review.backend):
        result = other.reproduce(result['case_id'], review.backend)
    assert result['state'] == 'reported'; assert result['last_attempt_status'] == 'replay_completed'
    assert result['last_attempt_disposition'] == 'baseline_already_passes'
    with pytest.raises(ValueError): other.draft(result['case_id'])


def test_reproduced_failure_does_not_require_an_observed_repair(review):
    review.fixture.change_config('after', 'memory', {'preserve':False})
    other = ReviewStore(review.root/'unfixed-store')
    result = other.propose(review.fixture.source, review.fixture.draft, review.fixture.spec, 'synthetic-author')
    with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend', return_value=review.backend):
        result = other.reproduce(result['case_id'], review.backend)
    assert result['state'] == 'reproduced'
    assert other.draft(result['case_id'])['state'] == 'drafted'


def test_artifact_mutation_quarantines_and_retains_old_events(review):
    review.qualified(); before = {p.name:p.read_bytes() for p in (review.case/'events').glob('*.json')}
    (review.attempt()/'replay/aa/baseline/recorded/workspace/original.txt').write_text('changed\n')
    with pytest.raises(ValueError, match='quarantined'): review.store.draft(review.case_id)
    assert review.store.show(review.case_id)['state'] == 'quarantined'
    for name, raw in before.items(): assert (review.case/'events'/name).read_bytes() == raw


def test_control_and_phase_mutations_cannot_be_reviewed(review):
    review.drafted(); (review.attempt()/'controls-source/4/original.txt').write_text('changed\n')
    with pytest.raises(ValueError, match='quarantined'): review.store.review(review.case_id, review.review_file())
    assert review.store.show(review.case_id)['state'] == 'quarantined'


def test_phase_capture_mutation_cannot_be_sealed(review):
    review.qualified(); (review.attempt()/'replay/aa/results.json').write_text('[]')
    with pytest.raises(ValueError, match='quarantined'): review.store.draft(review.case_id)
    assert review.store.show(review.case_id)['state'] == 'quarantined'


def test_private_case_summary_never_grants_publication_or_echoes_prose(review):
    value = json.loads(review.fixture.source.read_text()); value['source']['kind'] = 'private'
    review.fixture.source.write_text(json.dumps(value))
    descriptor = json.loads(review.fixture.draft.read_text()); descriptor['task']['source_kind'] = 'private'
    review.fixture.draft.write_text(json.dumps(descriptor)); review.fixture.bind()
    review.store = ReviewStore(review.root/'private-kind-store')
    result = review.store.propose(review.fixture.source, review.fixture.draft, review.fixture.spec, 'SYNTHETIC_PRIVATE_AUTHOR')
    review.case_id = result['case_id']
    path = review.review_file('quarantined', reason='SYNTHETIC_PRIVATE_REASON')
    result = review.store.review(review.case_id, path)
    assert result['privacy'] == 'private'; assert not result['publication_allowed']
    assert 'SYNTHETIC_PRIVATE' not in json.dumps(result)


def test_stale_review_head_or_evidence_and_self_session_only_reviews_are_rejected(review):
    review.drafted()
    for field in ('expected_event_sha256', 'evidence_sha256'):
        with pytest.raises(ValueError, match='stale'):
            review.store.review(review.case_id, review.review_file(**{field:'f'*64}))
    path = review.review_file(); value = json.loads(path.read_text())
    value['reviewer']['identity'] = 'SYNTHETIC-AUTHOR'; path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='separate reviewer'): review.store.review(review.case_id, path)
    path = review.review_file(); value = json.loads(path.read_text())
    value['reviewer']['basis_kind'] = 'fresh_model_session'; path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='fresh sessions'): review.store.review(review.case_id, path)
    assert review.store.show(review.case_id)['state'] == 'drafted'


def test_interrupted_attempt_requires_explicit_abandonment_before_fresh_reproduction(review):
    with patch('agent_harness_lab.evaluation.regression_review.replay', side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt): review.reproduce()
    assert review.store.show(review.case_id)['attempt_disposition'] == 'uncommitted'
    with pytest.raises(ValueError, match='intervention'): review.reproduce()
    result = review.store.review(review.case_id, review.review_file('reported'))
    assert result['last_attempt_status'] == 'abandoned'
    review.qualified()
    assert sum(e['kind']=='attempt_started' for e, _ in review.store.history(review.case)) == 2


def test_busy_lock_duplicate_proposal_and_corrupt_journal_never_overwrite_history(review):
    with review.store.locked():
        with pytest.raises(BlockingIOError): review.store.show(review.case_id)
    with pytest.raises(ValueError, match='already reported'):
        review.store.propose(review.fixture.source, review.fixture.draft, review.fixture.spec, 'other-author')
    path = review.case/'events/0001.json'; path.write_text('{broken')
    with pytest.raises(ValueError): review.store.show(review.case_id)
    assert path.read_text() == '{broken'


def test_attempt_reserves_completion_and_operator_capacity_before_dispatch(review):
    with review.store.locked():
        events = review.store.history(review.case)
        for index in range(31):
            review.store.append(review.case, events, 'attempt_started', 'reported', {'attempt':f'attempts/synthetic-{index}'})
            review.store.append(review.case, events, 'attempt_finished', 'reported', {'status':'blocked'})
    assert len(review.store.history(review.case)) == 63
    with patch('agent_harness_lab.evaluation.regression_review.replay', return_value={}) as execute:
        with pytest.raises(ValueError): review.reproduce()
    execute.assert_not_called()
    assert len(review.store.history(review.case)) == 63
    assert review.store.review(review.case_id, review.review_file('retired'))['state'] == 'retired'


def test_bounded_review_cannot_expand_into_a_poisoned_journal_event(review):
    path = review.review_file('quarantined'); value = json.loads(path.read_text())
    value['reviewer']['basis_kind'] = [{}]*20000
    path.write_text(json.dumps(value, separators=(',', ':')))
    assert path.stat().st_size < 64*1024
    with pytest.raises(ValueError): review.store.review(review.case_id, path)
    assert review.store.show(review.case_id)['state'] == 'reported'
    assert len(review.store.history(review.case)) == 1


def test_complete_event_bound_is_checked_before_writing(review):
    with review.store.locked():
        events = review.store.history(review.case)
        with pytest.raises(ValueError):
            review.store.append(review.case, events, 'quarantined', 'quarantined', {'reason':'x'*(300*1024)})
    assert review.store.show(review.case_id)['state'] == 'reported'


def test_linked_attempt_parent_cannot_write_outside_controller_store(review):
    outside = review.root/'outside'; outside.mkdir()
    (review.case/'attempts').symlink_to(outside)
    with pytest.raises(ValueError): review.reproduce()
    assert list(outside.iterdir()) == []
    review.backend.run.assert_not_called()


def test_concurrent_duplicate_intake_has_one_immutable_winner(review):
    store = ReviewStore(review.root/'concurrent-store')
    def propose(author):
        try: return store.propose(review.fixture.source, review.fixture.draft, review.fixture.spec, author)
        except (ValueError, BlockingIOError): return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(propose, ('author-a','author-b')))
    winners = [r for r in results if r is not None]
    assert len(winners) == 1
    assert len(store.history(store.case(winners[0]['case_id']))) == 1


def test_store_overlap_and_nonprivate_or_linked_locations_are_rejected(review):
    original = digest(review.root/'oracle')
    store = ReviewStore(review.root/'oracle/store')
    with pytest.raises(ValueError, match='overlap'):
        store.propose(review.fixture.source, review.fixture.draft, review.fixture.spec, 'author')
    assert digest(review.root/'oracle') == original
    review.store.root.chmod(0o755)
    with pytest.raises(ValueError, match='owner-only'): review.store.show(review.case_id)
    review.store.root.chmod(0o700)
    alias = review.root/'alias'; alias.symlink_to(review.store.root)
    with pytest.raises(ValueError, match='symlink'): ReviewStore(alias).show(review.case_id)


def test_read_operations_do_not_initialize_an_unrelated_private_directory(review):
    folder = review.root/'unrelated'; folder.mkdir(mode=0o700)
    (folder/'bank.json').write_text('{"synthetic":"preserve"}')
    with pytest.raises((ValueError, OSError)): ReviewStore(folder).show(review.case_id)
    assert sorted(p.name for p in folder.iterdir()) == ['bank.json']


def test_operator_cannot_forge_internal_lifecycle_states(review):
    for state in ('reproduced', 'drafted', 'accepted', 'attempt_started'):
        with pytest.raises(ValueError, match='decision'):
            review.store.review(review.case_id, review.review_file(state))
    assert review.store.show(review.case_id)['state'] == 'reported'


def test_parent_budget_exhausted_before_dispatch_stays_unreproduced(review):
    from agent_harness_lab.evaluation.regression_review import copy_frozen
    clock = [0]
    def slow(*args):
        result = copy_frozen(*args); clock[0] = 2; return result
    with patch('agent_harness_lab.evaluation.regression_review.time.monotonic', side_effect=lambda:clock[0]), \
         patch('agent_harness_lab.evaluation.regression_review.copy_frozen', side_effect=slow):
        result = review.reproduce(budget_seconds=1)
    assert result['state'] == 'reported'; assert result['last_attempt_status'] == 'budget_exhausted'
    review.backend.run.assert_not_called()


def test_cli_show_and_errors_do_not_emit_private_locators_or_prose(review):
    args = ['regression-review', '--store', str(review.store.root)]
    with patch('builtins.print') as out:
        assert main(args+['show','--case',review.case_id]) == 0
    text = out.call_args.args[0]
    assert str(review.root) not in text; assert 'synthetic-author' not in text
    with pytest.raises(SystemExit) as exit:
        main(args+['review','--case',review.case_id,'--record',str(review.root/'SYNTHETIC_PRIVATE_FILE')])
    assert exit.value.code == 2
