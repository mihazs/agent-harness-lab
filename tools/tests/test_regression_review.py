"""Synthetic controller cases and operator attestations, not authenticated reviewers."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.regression_review import ReviewStore
if __package__:
    from . import test_regression_replay as fixtures
else:
    import test_regression_replay as fixtures


class ReviewFixture(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RegressionReplayTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.backend = self.fixture.root, self.fixture.backend
        self.store = ReviewStore(self.root/'private-store')
        self.initial = self.store.propose(self.fixture.source, self.fixture.draft, self.fixture.spec, 'synthetic-author')
        self.case_id = self.initial['case_id']; self.case = self.store.case(self.case_id)

    def reproduce(self, **kwargs):
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend', return_value=self.backend):
            return self.store.reproduce(self.case_id, self.backend, **kwargs)

    def qualified(self):
        result = self.reproduce(); self.assertEqual(result['state'], 'reproduced', result)
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


class RegressionReviewTests(ReviewFixture):
    def test_reported_reproduced_drafted_reviewed_chain_preserves_limits_and_original_inputs(self):
        original = {p:digest(self.root/p) for p in ('fixture','oracle','before','after','recorded')}
        self.assertEqual(self.initial['state'], 'reported'); self.backend.run.assert_not_called()
        self.qualified(); self.store.draft(self.case_id)
        result = self.store.review(self.case_id, self.review_file())
        self.assertEqual(result['state'], 'reviewed')
        self.assertEqual(result['review_independence'], 'operator_attested')
        self.assertEqual([e['kind'] for e, _ in self.store.history(self.case)],
                         ['reported','attempt_started','reproduced','drafted','reviewed'])
        self.assertEqual(original, {p:digest(self.root/p) for p in original})
        for key in ('bank_admission', 'publication_allowed', 'automatic_promotion'): self.assertFalse(result[key])
        self.assertEqual(result['historical_verification'], 'unverified'); self.assertEqual(result['independence'], 'unverified')
        self.assertEqual((self.store.root.stat().st_mode & 0o777), 0o700)
        for path in [self.case/'inputs.json', *list((self.case/'events').glob('*.json'))]:
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.case/'proposal.json').read_bytes(), self.fixture.source.read_bytes())

    def test_source_declaration_change_quarantines_without_execution(self):
        self.fixture.draft.write_text(self.fixture.draft.read_text()+' ')
        with self.assertRaisesRegex(ValueError, 'quarantined'): self.reproduce()
        self.assertEqual(self.store.show(self.case_id)['state'], 'quarantined')
        self.backend.run.assert_not_called()

    def test_caller_success_object_without_controller_artifacts_cannot_reproduce(self):
        with patch('agent_harness_lab.evaluation.regression_review.replay', return_value={'status':'replay_completed'}):
            result = self.reproduce()
        self.assertEqual(result['state'], 'reported'); self.assertEqual(result['last_attempt_status'], 'incomplete')
        self.assertIsNone(result['evidence_sha256'])

    @unittest.skipUnless(Path('/proc/self/fd').is_dir(), 'requires Linux descriptor fault injection')
    def test_critical_artifact_durability_failure_never_commits_reproduction(self):
        fsync = os.fsync
        def fail_data(descriptor):
            path = Path(os.readlink('/proc/self/fd/'+str(descriptor)))
            if 'controls-source' in path.parts: raise OSError('SYNTHETIC_DATA_FSYNC_FAILURE')
            return fsync(descriptor)
        with patch('os.fsync', side_effect=fail_data): result = self.reproduce()
        self.assertEqual(result['state'], 'reported'); self.assertEqual(result['last_attempt_status'], 'incomplete')
        self.assertIsNone(result['evidence_sha256'])

    def test_baseline_already_passing_never_advances_lifecycle(self):
        self.fixture.change_config('before', 'config', {'enabled':True})
        other = ReviewStore(self.root/'other-store')
        result = other.propose(self.fixture.source, self.fixture.draft, self.fixture.spec, 'synthetic-author')
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend', return_value=self.backend):
            result = other.reproduce(result['case_id'], self.backend)
        self.assertEqual(result['state'], 'reported'); self.assertEqual(result['last_attempt_status'], 'replay_completed')
        self.assertEqual(result['last_attempt_disposition'], 'baseline_already_passes')
        with self.assertRaises(ValueError): other.draft(result['case_id'])

    def test_reproduced_failure_does_not_require_an_observed_repair(self):
        self.fixture.change_config('after', 'memory', {'preserve':False})
        other = ReviewStore(self.root/'unfixed-store')
        result = other.propose(self.fixture.source, self.fixture.draft, self.fixture.spec, 'synthetic-author')
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend', return_value=self.backend):
            result = other.reproduce(result['case_id'], self.backend)
        self.assertEqual(result['state'], 'reproduced')
        self.assertEqual(other.draft(result['case_id'])['state'], 'drafted')

    def test_artifact_mutation_quarantines_and_retains_old_events(self):
        self.qualified(); before = {p.name:p.read_bytes() for p in (self.case/'events').glob('*.json')}
        (self.attempt()/'replay/aa/baseline/recorded/workspace/original.txt').write_text('changed\n')
        with self.assertRaisesRegex(ValueError, 'quarantined'): self.store.draft(self.case_id)
        self.assertEqual(self.store.show(self.case_id)['state'], 'quarantined')
        for name, raw in before.items(): self.assertEqual((self.case/'events'/name).read_bytes(), raw)

    def test_control_and_phase_mutations_cannot_be_reviewed(self):
        self.drafted(); (self.attempt()/'controls-source/4/original.txt').write_text('changed\n')
        with self.assertRaisesRegex(ValueError, 'quarantined'): self.store.review(self.case_id, self.review_file())
        self.assertEqual(self.store.show(self.case_id)['state'], 'quarantined')

    def test_phase_capture_mutation_cannot_be_sealed(self):
        self.qualified(); (self.attempt()/'replay/aa/results.json').write_text('[]')
        with self.assertRaisesRegex(ValueError, 'quarantined'): self.store.draft(self.case_id)
        self.assertEqual(self.store.show(self.case_id)['state'], 'quarantined')

    def test_private_case_summary_never_grants_publication_or_echoes_prose(self):
        value = json.loads(self.fixture.source.read_text()); value['source']['kind'] = 'private'
        self.fixture.source.write_text(json.dumps(value))
        descriptor = json.loads(self.fixture.draft.read_text()); descriptor['task']['source_kind'] = 'private'
        self.fixture.draft.write_text(json.dumps(descriptor)); self.fixture.bind()
        self.store = ReviewStore(self.root/'private-kind-store')
        result = self.store.propose(self.fixture.source, self.fixture.draft, self.fixture.spec, 'SYNTHETIC_PRIVATE_AUTHOR')
        self.case_id = result['case_id']
        path = self.review_file('quarantined', reason='SYNTHETIC_PRIVATE_REASON')
        result = self.store.review(self.case_id, path)
        self.assertEqual(result['privacy'], 'private'); self.assertFalse(result['publication_allowed'])
        self.assertNotIn('SYNTHETIC_PRIVATE', json.dumps(result))

    def test_stale_review_head_or_evidence_and_self_session_only_reviews_are_rejected(self):
        self.drafted()
        for field in ('expected_event_sha256', 'evidence_sha256'):
            with self.assertRaisesRegex(ValueError, 'stale'):
                self.store.review(self.case_id, self.review_file(**{field:'f'*64}))
        path = self.review_file(); value = json.loads(path.read_text())
        value['reviewer']['identity'] = 'SYNTHETIC-AUTHOR'; path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'separate reviewer'): self.store.review(self.case_id, path)
        path = self.review_file(); value = json.loads(path.read_text())
        value['reviewer']['basis_kind'] = 'fresh_model_session'; path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'fresh sessions'): self.store.review(self.case_id, path)
        self.assertEqual(self.store.show(self.case_id)['state'], 'drafted')

    def test_interrupted_attempt_requires_explicit_abandonment_before_fresh_reproduction(self):
        with patch('agent_harness_lab.evaluation.regression_review.replay', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt): self.reproduce()
        self.assertEqual(self.store.show(self.case_id)['attempt_disposition'], 'uncommitted')
        with self.assertRaisesRegex(ValueError, 'intervention'): self.reproduce()
        result = self.store.review(self.case_id, self.review_file('reported'))
        self.assertEqual(result['last_attempt_status'], 'abandoned')
        self.qualified()
        self.assertEqual(sum(e['kind']=='attempt_started' for e, _ in self.store.history(self.case)), 2)

    def test_busy_lock_duplicate_proposal_and_corrupt_journal_never_overwrite_history(self):
        with self.store.locked():
            with self.assertRaises(BlockingIOError): self.store.show(self.case_id)
        with self.assertRaisesRegex(ValueError, 'already reported'):
            self.store.propose(self.fixture.source, self.fixture.draft, self.fixture.spec, 'other-author')
        path = self.case/'events/0001.json'; path.write_text('{broken')
        with self.assertRaises(ValueError): self.store.show(self.case_id)
        self.assertEqual(path.read_text(), '{broken')

    def test_attempt_reserves_completion_and_operator_capacity_before_dispatch(self):
        with self.store.locked():
            events = self.store.history(self.case)
            for index in range(31):
                self.store.append(self.case, events, 'attempt_started', 'reported', {'attempt':f'attempts/synthetic-{index}'})
                self.store.append(self.case, events, 'attempt_finished', 'reported', {'status':'blocked'})
        self.assertEqual(len(self.store.history(self.case)), 63)
        with patch('agent_harness_lab.evaluation.regression_review.replay', return_value={}) as execute:
            with self.assertRaises(ValueError): self.reproduce()
        execute.assert_not_called()
        self.assertEqual(len(self.store.history(self.case)), 63)
        self.assertEqual(self.store.review(self.case_id, self.review_file('retired'))['state'], 'retired')

    def test_bounded_review_cannot_expand_into_a_poisoned_journal_event(self):
        path = self.review_file('quarantined'); value = json.loads(path.read_text())
        value['reviewer']['basis_kind'] = [{}]*20000
        path.write_text(json.dumps(value, separators=(',', ':')))
        self.assertLess(path.stat().st_size, 64*1024)
        with self.assertRaises(ValueError): self.store.review(self.case_id, path)
        self.assertEqual(self.store.show(self.case_id)['state'], 'reported')
        self.assertEqual(len(self.store.history(self.case)), 1)

    def test_complete_event_bound_is_checked_before_writing(self):
        with self.store.locked():
            events = self.store.history(self.case)
            with self.assertRaises(ValueError):
                self.store.append(self.case, events, 'quarantined', 'quarantined', {'reason':'x'*(300*1024)})
        self.assertEqual(self.store.show(self.case_id)['state'], 'reported')

    def test_linked_attempt_parent_cannot_write_outside_controller_store(self):
        outside = self.root/'outside'; outside.mkdir()
        (self.case/'attempts').symlink_to(outside)
        with self.assertRaises(ValueError): self.reproduce()
        self.assertEqual(list(outside.iterdir()), [])
        self.backend.run.assert_not_called()

    def test_concurrent_duplicate_intake_has_one_immutable_winner(self):
        store = ReviewStore(self.root/'concurrent-store')
        def propose(author):
            try: return store.propose(self.fixture.source, self.fixture.draft, self.fixture.spec, author)
            except (ValueError, BlockingIOError): return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(propose, ('author-a','author-b')))
        winners = [r for r in results if r is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(len(store.history(store.case(winners[0]['case_id']))), 1)

    def test_store_overlap_and_nonprivate_or_linked_locations_are_rejected(self):
        original = digest(self.root/'oracle')
        store = ReviewStore(self.root/'oracle/store')
        with self.assertRaisesRegex(ValueError, 'overlap'):
            store.propose(self.fixture.source, self.fixture.draft, self.fixture.spec, 'author')
        self.assertEqual(digest(self.root/'oracle'), original)
        self.store.root.chmod(0o755)
        with self.assertRaisesRegex(ValueError, 'owner-only'): self.store.show(self.case_id)
        self.store.root.chmod(0o700)
        alias = self.root/'alias'; alias.symlink_to(self.store.root)
        with self.assertRaisesRegex(ValueError, 'symlink'): ReviewStore(alias).show(self.case_id)

    def test_read_operations_do_not_initialize_an_unrelated_private_directory(self):
        folder = self.root/'unrelated'; folder.mkdir(mode=0o700)
        (folder/'bank.json').write_text('{"synthetic":"preserve"}')
        with self.assertRaises((ValueError, OSError)): ReviewStore(folder).show(self.case_id)
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ['bank.json'])

    def test_operator_cannot_forge_internal_lifecycle_states(self):
        for state in ('reproduced', 'drafted', 'accepted', 'attempt_started'):
            with self.assertRaisesRegex(ValueError, 'decision'):
                self.store.review(self.case_id, self.review_file(state))
        self.assertEqual(self.store.show(self.case_id)['state'], 'reported')

    def test_parent_budget_exhausted_before_dispatch_stays_unreproduced(self):
        from agent_harness_lab.evaluation.regression_review import copy_frozen
        clock = [0]
        def slow(*args):
            result = copy_frozen(*args); clock[0] = 2; return result
        with patch('agent_harness_lab.evaluation.regression_review.time.monotonic', side_effect=lambda:clock[0]), \
             patch('agent_harness_lab.evaluation.regression_review.copy_frozen', side_effect=slow):
            result = self.reproduce(budget_seconds=1)
        self.assertEqual(result['state'], 'reported'); self.assertEqual(result['last_attempt_status'], 'budget_exhausted')
        self.backend.run.assert_not_called()

    def test_cli_show_and_errors_do_not_emit_private_locators_or_prose(self):
        args = ['regression-review', '--store', str(self.store.root)]
        with patch('builtins.print') as out:
            self.assertEqual(main(args+['show','--case',self.case_id]), 0)
        text = out.call_args.args[0]
        self.assertNotIn(str(self.root), text); self.assertNotIn('synthetic-author', text)
        with self.assertRaises(SystemExit) as exit:
            main(args+['review','--case',self.case_id,'--record',str(self.root/'SYNTHETIC_PRIVATE_FILE')])
        self.assertEqual(exit.exception.code, 2)
