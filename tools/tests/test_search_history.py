"""Guarded search orchestration; core doubles below are not native authentication."""
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import asdict
import fcntl
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_harness_lab.optimization.budget import Budget
from agent_harness_lab.optimization.history_evidence import HistorySearchContext
from agent_harness_lab.optimization.search import CandidateRecord, search
from agent_harness_lab.private_journal import encoded, record


def candidate(name='baseline', content=None):
    return CandidateRecord(name, (), 'synthetic-mechanism', {} if content is None else {'AGENTS.md': content},
                           ('synthetic:public-control',), 'Synthetic hypothesis', 'Synthetic falsifier')


class SearchHistoryBranchTests(unittest.TestCase):
    """Patch the trusted context core explicitly to exercise orchestration branches."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'state/search.json'
        self.context = object.__new__(HistorySearchContext)
        self.template = dict(schema_version=1, algorithm='guarded-preservation-search-v1', template_sha256='a'*64,
            baseline_sha256='b'*64, allowed_files=['AGENTS.md'], resource_profile=dict(budget_seconds=60),
            paths={name: str(self.root/name) for name in ('store', 'baseline', 'work', 'bank_owner', 'image')})
        self.template['paths']['manifest'] = str(self.root/'export/manifest.json')
        self.calls, self.verifications, self.saved = [], [], {}
        self.revoked, self.interrupt, self.sparse = False, False, False
        self.on_verified = None
        stack = ExitStack(); self.addCleanup(stack.close)
        stack.enter_context(patch.object(HistorySearchContext, 'template_binding', property(lambda context: deepcopy(self.template))))
        stack.enter_context(patch.object(HistorySearchContext, 'evaluate', lambda context, *a, **kw: self.evaluate(*a, **kw)))
        stack.enter_context(patch.object(HistorySearchContext, 'verify', lambda context, binding: self.verify(binding)))

    def evaluate(self, value, *, stage, event_id, on_started=None, parent_deadline=None):
        before = record(self.path, 2*1024*1024)[0]
        self.assertIn(event_id, {event['event_id'] for event in before['budget']['events']})
        self.assertIsNotNone(parent_deadline)
        self.calls.append(stage)
        ident = hashlib.sha256(str(len(self.calls)).encode()).hexdigest()
        on_started(ident)
        during = record(self.path, 2*1024*1024)[0]
        attempt = during['confirmation'] if stage == 'development_confirmation' else during['attempts'][-1]
        self.assertEqual(attempt['run_id'], ident)
        if self.interrupt:
            raise KeyboardInterrupt('synthetic interruption after actual custody callback')
        binding = dict(schema_version=1, template_sha256=self.template['template_sha256'],
            candidate_record=json.loads(encoded(asdict(value))), patch_sha256=value.digest,
            harness_sha256='b'*64 if stage == 'baseline' else 'c'*64, tree_sha256='d'*64,
            stage=stage, event_id=event_id, attempt_id=hashlib.sha256(('attempt'+ident).encode()).hexdigest(),
            run_id=ident, receipt_sha256='e'*64, plan_sha256='f'*64, manifest_sha256='1'*64)
        self.saved[ident] = deepcopy(binding)
        # Unverified return labels must not rank the candidate; verify() supplies fresh facts.
        return dict(binding=binding, safe=False, metrics={'invented_cost': -999})

    def verify(self, binding):
        if self.revoked or not isinstance(binding, dict) or self.saved.get(binding.get('run_id')) != binding:
            raise ValueError('synthetic revoked or forged actual binding')
        self.verifications.append(binding['run_id'])
        if self.on_verified is not None:
            self.on_verified(binding)
        baseline = binding['stage'] == 'baseline'
        feasible = not (baseline and self.sparse)
        return dict(binding=deepcopy(binding), safe=feasible, qualified_feasible=feasible,
                    retain_no_op=baseline, metrics={'primary_effect': 0 if baseline else .5} if feasible else {},
                    verification='current_recomputed', publication_allowed=False, automatic_promotion=False, adoption_authority=False)

    def run_search(self, *, budget=None, propose=None, **options):
        return search(self.path, candidate(), propose or (lambda index: candidate('patch'+str(index), 'fixed'+str(index))),
            lambda value: self.fail('raw evaluation callback must not run'),
            lambda value: self.fail('raw certification callback must not run'), budget or Budget(6, 1), ['AGENTS.md'],
            enabled=True, variants=1, controller=self.context, **options)

    def test_fresh_confirmation_and_cached_return_use_only_fresh_core_evidence(self):
        budget = Budget(6, 1)
        value = self.run_search(budget=budget)
        self.assertEqual(value['status'], 'development_recommended_for_review')
        self.assertEqual(self.calls, ['baseline', 'development', 'development_confirmation'])
        self.assertEqual(budget.spent, 5)
        self.assertTrue(budget.events[-1]['final'])
        self.assertEqual(budget.events[-1]['kind'], 'development_confirmation')
        confirmation = value['confirmation']['binding']
        self.assertTrue(all(confirmation['run_id'] != a['binding']['run_id'] for a in value['attempts']))
        self.assertEqual(value['candidates'][1]['metrics'], {'primary_effect': .5})
        before = len(self.verifications)
        resumed = self.run_search()
        self.assertEqual(resumed['status'], 'development_recommended_for_review')
        self.assertTrue(resumed['resumed_without_execution'])
        self.assertGreater(len(self.verifications), before)
        self.assertEqual(len(self.calls), 3)
        self.assertFalse(resumed['automatic_promotion'])
        self.assertFalse(resumed['publication_allowed'])
        self.assertFalse(resumed['adoption_authority'])
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_interruption_preserves_charge_and_custody_without_redispatch(self):
        budget = Budget(6, 1); self.interrupt = True
        with self.assertRaises(KeyboardInterrupt): self.run_search(budget=budget)
        saved = record(self.path)[0]
        self.assertEqual(budget.spent, 2)
        self.assertIsNotNone(saved['attempts'][0]['run_id'])
        self.interrupt = False
        fresh = Budget(6, 1)
        resumed = self.run_search(budget=fresh)
        self.assertEqual(resumed['status'], 'interrupted')
        self.assertEqual(fresh.spent, 2)
        self.assertEqual(self.calls, ['baseline'])

    def test_sparse_baseline_retains_no_op_without_feasibility_claim(self):
        self.sparse = True
        value = self.run_search(propose=lambda index: self.fail('sparse qualification should preserve no-op'))
        self.assertEqual(value['status'], 'no_change')
        self.assertFalse(value['candidates'][0]['safe'])
        self.assertEqual(self.calls, ['baseline'])
        self.assertEqual(self.run_search()['status'], 'no_change')
        self.assertEqual(self.calls, ['baseline'])

    def test_revoked_receipt_or_wrong_context_blocks_cached_favorable_status(self):
        self.run_search()
        self.revoked = True
        value = self.run_search()
        self.assertEqual(value['status'], 'blocked')
        self.assertEqual(len(self.calls), 3)
        self.revoked = False
        self.template['template_sha256'] = '2'*64
        with self.assertRaisesRegex(ValueError, 'context'):
            self.run_search()

    def test_forged_binding_and_removed_budget_charges_cannot_resume(self):
        self.run_search()
        original = self.path.read_bytes()
        for mutate in (lambda state:state['attempts'][1]['binding'].update(harness_sha256='9'*64),
                       lambda state:state['budget']['events'].pop(),
                       lambda state:state.update(publication_allowed=True),
                       lambda state:state.update(schema_version=1)):
            state = json.loads(original); mutate(state); self.path.write_bytes(encoded(state))
            try:
                value = self.run_search()
            except ValueError:
                pass
            else:
                self.assertEqual(value['status'], 'blocked')
            self.assertEqual(len(self.calls), 3)
        self.path.write_bytes(original)
        state = json.loads(original); state.update(status='certified', reason='forged label')
        self.path.write_bytes(encoded(state))
        self.assertEqual(self.run_search()['status'], 'development_recommended_for_review')

    def test_candidate_mutation_is_detected_and_no_confirmation_runs(self):
        original = self.evaluate
        def mutate(value, **kwargs):
            result = original(value, **kwargs)
            if value.changes: value.changes['AGENTS.md'] = 'mutated after controller freeze'
            return result
        self.evaluate = mutate
        value = self.run_search()
        self.assertEqual(value['status'], 'blocked')
        self.assertEqual(self.calls, ['baseline', 'development'])
        self.assertNotEqual(self.run_search()['status'], 'development_recommended_for_review')
        self.assertEqual(len(self.calls), 2)

    def test_confirmation_interruption_never_spends_again(self):
        original = self.evaluate
        def interrupt(value, **kwargs):
            if kwargs['stage'] == 'development_confirmation': self.interrupt = True
            return original(value, **kwargs)
        self.evaluate = interrupt
        with self.assertRaises(KeyboardInterrupt): self.run_search()
        state = record(self.path)[0]
        self.assertEqual(state['confirmation']['stage'], 'run_started')
        before = list(state['budget']['events'])
        resumed = self.run_search()
        self.assertEqual(resumed['status'], 'interrupted')
        self.assertEqual(resumed['budget']['events'], before)
        self.assertEqual(len(self.calls), 3)

    def test_no_duplicate_confirmation_custody_can_be_favorable(self):
        self.run_search()
        state = record(self.path)[0]
        state['confirmation']['run_id'] = state['attempts'][1]['run_id']
        state['confirmation']['binding'] = state['attempts'][1]['binding']
        self.path.write_bytes(encoded(state))
        self.assertEqual(self.run_search()['status'], 'blocked')
        self.assertEqual(len(self.calls), 3)

    def test_stage_budget_keeps_reserve_and_never_refunds_undispatched_charge(self):
        budget = Budget(4, 1)
        value = self.run_search(budget=budget)
        self.assertEqual(value['status'], 'budget_exhausted')
        self.assertEqual(budget.spent, 3)
        self.assertTrue(all(not event['final'] for event in budget.events))
        self.assertEqual(self.calls, ['baseline'])
        resumed_budget = Budget(4, 1)
        resumed = self.run_search(budget=resumed_budget)
        self.assertEqual(resumed['status'], 'interrupted')
        self.assertEqual(resumed_budget.spent, 3)
        self.assertEqual(self.calls, ['baseline'])

    def test_concurrent_search_lock_prevents_a_second_dispatch(self):
        self.path.parent.mkdir(mode=0o700)
        lock = self.path.with_name(self.path.name + '.lock')
        lock.touch(mode=0o600)
        with lock.open('r+') as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                self.run_search()
        self.assertEqual(self.calls, [])
        self.assertFalse(self.path.exists())

    def test_final_verification_cannot_extend_parent_deadline_on_new_or_cached_return(self):
        clock = [0]
        def advance(binding):
            if binding['stage'] == 'development_confirmation': clock[0] = 11
        self.on_verified = advance
        with patch('agent_harness_lab.optimization.search.time', SimpleNamespace(monotonic=lambda: clock[0])):
            value = self.run_search(parent_deadline=10)
            self.assertEqual(value['status'], 'budget_exhausted')
            clock[0] = 0
            cached = self.run_search(parent_deadline=10)
            self.assertEqual(cached['status'], 'budget_exhausted')
        self.assertEqual(self.calls, ['baseline', 'development', 'development_confirmation'])

    def test_default_parent_clock_includes_live_context_preflight(self):
        clock = [0]
        def slow(context):
            clock[0] = 61
            return deepcopy(self.template)
        with patch.object(HistorySearchContext, 'template_binding', property(slow)), \
                patch('agent_harness_lab.optimization.search.time', SimpleNamespace(monotonic=lambda: clock[0])):
            value = self.run_search()
        self.assertEqual(value['status'], 'budget_exhausted')
        self.assertEqual(self.calls, [])

    def test_expired_parent_deadline_never_dispatches(self):
        value = self.run_search(parent_deadline=0)
        self.assertEqual(value['status'], 'budget_exhausted')
        self.assertEqual(self.calls, [])
        self.assertEqual(value['budget']['events'], [])

    def test_wrong_controller_and_schema_one_cannot_supply_guarded_authority(self):
        with self.assertRaisesRegex(ValueError, 'actual history'):
            search(self.path, candidate(), None, None, None, Budget(6, 1), ['AGENTS.md'], enabled=True, controller=object())
        self.path.parent.mkdir()
        self.path.write_bytes(encoded(dict(schema_version=1, status='recommended')))
        self.path.chmod(0o600)
        with self.assertRaises(ValueError): self.run_search()
        self.assertEqual(self.calls, [])

    def test_private_path_and_lock_boundaries(self):
        self.path.parent.mkdir(); self.path.parent.chmod(0o755)
        with self.assertRaisesRegex(ValueError, 'owner-only'): self.run_search()
        self.path.parent.chmod(0o700)
        self.path = Path(self.template['paths']['baseline'])/'search.json'
        with self.assertRaisesRegex(ValueError, 'outside'): self.run_search()

    def test_callback_route_stays_explicitly_unverified(self):
        value = search(self.path, candidate(), lambda index: candidate('patch', 'fixed'),
            lambda value: dict(safe=True, metrics={'effect': bool(value.changes)}),
            lambda value: dict(status='recommended'), Budget(6, 1), ['AGENTS.md'], enabled=True, variants=1)
        self.assertEqual(value['evidence_source'], 'unverified_callback')
        persisted = record(self.path)[0]
        self.assertEqual(persisted['evidence_source'], 'unverified_callback')
        self.assertFalse(persisted['publication_allowed'])
        self.assertFalse(persisted['adoption_authority'])
        self.assertFalse(value['publication_allowed'])
        self.assertFalse(value['adoption_authority'])


class SearchActualHistoryTests(unittest.TestCase):
    """Actual public synthetic subprocess evidence; isolation attestations are simulated."""
    def test_sparse_actual_receipt_retains_no_op_then_revocation_blocks_cached_return(self):
        if __package__:
            from . import test_preservation_history as fixtures
        else:
            import test_preservation_history as fixtures
        helper = fixtures.PreservationHistoryTests()
        helper.setUp(); self.addCleanup(helper.doCleanups)
        f = helper.f
        context = HistorySearchContext(str(f.store.root), str(f.baseline), str(f.export/'manifest.json'),
            dict(cgroup_root=str(f.root/'delegation'), image_root=str(f.root/'image'), image_sha256='e'*64,
                 disk_bytes=64*1024*1024, budget_seconds=60, work_root=str(f.root/'search-work')), ['solve.py'])
        path = f.root/'search-state/search.json'
        baseline = CandidateRecord('actual-baseline', (), 'synthetic-case', {}, ('synthetic:control',),
                                   'Keep existing behavior', 'Any preservation violation')
        def forbidden(*args): self.fail('sparse source evidence cannot invoke raw callbacks or propose')
        with self.workers(f):
            result = search(path, baseline, forbidden, forbidden, forbidden, Budget(6, 1), ['solve.py'],
                            enabled=True, variants=1, controller=context)
        self.assertEqual(result['status'], 'no_change')
        self.assertFalse(result['candidates'][0]['safe'])
        binding = result['attempts'][0]['binding']
        receipt = f.store.verify_decision(binding['run_id'], binding['receipt_sha256'])
        self.assertEqual(receipt['analysis']['status'], 'inconclusive')
        self.assertFalse(result['publication_allowed'])
        with patch.object(context, 'evaluate', side_effect=AssertionError('resume must not dispatch')):
            resumed = search(path, baseline, forbidden, forbidden, forbidden, Budget(6, 1), ['solve.py'],
                             enabled=True, variants=1, controller=context)
        self.assertEqual(resumed['status'], 'no_change')
        self.assertTrue(resumed['resumed_without_execution'])
        f.revoke()
        with self.assertRaises(ValueError):
            search(path, baseline, forbidden, forbidden, forbidden, Budget(6, 1), ['solve.py'],
                   enabled=True, variants=1, controller=context)

    def workers(self, fixture):
        from agent_harness_lab.evaluation.sandbox import SandboxBackend
        stack = ExitStack()
        stack.enter_context(patch.object(SandboxBackend, 'capability', return_value={'status': 'available'}))
        stack.enter_context(patch.object(SandboxBackend, 'run', side_effect=fixture.execute))
        return stack
