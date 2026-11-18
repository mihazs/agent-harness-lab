"""Registered strategies through the guarded controller route; the controller core is a synthetic double."""
# fixed-in: 0c6f6e40 -k freezes_each_prediction
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from unittest.mock import patch

import pytest

from agent_harness_lab.diagnostics.predictions import PredictionLedger, PredictionRecord
from agent_harness_lab.optimization import strategies
from agent_harness_lab.optimization.budget import Budget
from agent_harness_lab.optimization.history_evidence import HistorySearchContext
from agent_harness_lab.optimization.search import CandidateRecord
from agent_harness_lab.optimization.strategies import compare_strategies, run_strategy
from agent_harness_lab.private_journal import encoded

EVIDENCE = [dict(scope='development', ref='e1')]
FILES = ['AGENTS.md']
BASE = CandidateRecord('base', (), 'm', {}, ('e1',), 'hypothesis', 'falsifier')


def candidate(name, content):
    return CandidateRecord(name, (), 'm', {'AGENTS.md': content}, ('e1',), 'hypothesis', 'falsifier')


def proposer(request):
    return candidate(f'p{request["index"]}', f'text {request["index"]}')


class Controller:
    """Stands in for the trusted history context: verified effects come from `effects`, never from a return value."""
    def __init__(self, root):
        self.root, self.calls, self.saved = root, [], {}
        self.effects, self.unsafe, self.interrupt_on = dict(p1=.5, p2=.1, p3=.1), set(), None
        self.retry_effects, self.unqualified_retries, self.revoke_retries, self.crash_on = {}, set(), False, None
        self.template = dict(schema_version=1, algorithm='guarded-preservation-search-v1', template_sha256='a'*64,
            baseline_sha256='b'*64, allowed_files=['AGENTS.md'], resource_profile=dict(budget_seconds=60),
            paths={name: str(root/name) for name in ('store', 'baseline', 'work', 'bank_owner', 'image')})
        self.template['paths']['manifest'] = str(root/'export/manifest.json')

    def evaluate(self, value, *, stage, event_id, on_started=None, parent_deadline=None):
        self.calls.append((value.candidate_id, stage))
        ident = hashlib.sha256(str(len(self.calls)).encode()).hexdigest()
        on_started(ident)
        if self.interrupt_on == stage:
            raise KeyboardInterrupt('synthetic interruption after the custody callback')
        if self.crash_on == stage:
            raise RuntimeError('synthetic controller failure after the custody callback')
        binding = dict(schema_version=1, template_sha256=self.template['template_sha256'],
            candidate_record=json.loads(encoded(asdict(value))), patch_sha256=value.digest,
            harness_sha256='b'*64, tree_sha256='d'*64, stage=stage, event_id=event_id,
            attempt_id=hashlib.sha256(('attempt'+ident).encode()).hexdigest(), run_id=ident,
            receipt_sha256='e'*64, plan_sha256='f'*64, manifest_sha256='1'*64)
        self.saved[ident] = deepcopy(binding)
        return dict(binding=binding, safe=True, metrics={'invented_effect': 999})  # Unverified labels must be ignored.

    def verify(self, binding):
        assert self.saved.get(binding['run_id']) == binding
        if binding['stage'] == 'baseline_retry':  # An unchanged comparison reports its observed effect, none when unqualified.
            if self.revoke_retries:
                raise ValueError('synthetic revoked retry receipt')
            number = int(binding['event_id'].rsplit(':', 1)[1])
            qualified = number not in self.unqualified_retries
            return dict(binding=deepcopy(binding), safe=qualified, qualified_feasible=qualified, retain_no_op=True,
                        metrics={'primary_effect': self.retry_effects.get(number, 0)} if qualified else {})
        name = binding['candidate_record']['candidate_id']
        safe = name not in self.unsafe and not (binding['stage'] == 'development_confirmation' and 'confirm' in self.unsafe)
        return dict(binding=deepcopy(binding), safe=safe, qualified_feasible=safe, retain_no_op=name == 'base',
                    metrics={'primary_effect': self.effects.get(name, 0)} if safe else {})


@pytest.fixture
def controller(tmp_path):
    fake = Controller(tmp_path)
    with ExitStack() as stack:
        stack.enter_context(patch.object(HistorySearchContext, 'template_binding', property(lambda context: deepcopy(fake.template))))
        stack.enter_context(patch.object(HistorySearchContext, 'evaluate', lambda context, *a, **kw: fake.evaluate(*a, **kw)))
        stack.enter_context(patch.object(HistorySearchContext, 'verify', lambda context, binding: fake.verify(binding)))
        fake.context = object.__new__(HistorySearchContext)
        yield fake


def compare(tmp_path, fake, propose=proposer, ceiling=12, reserve=1, **options):
    options.setdefault('controller', fake.context)
    return compare_strategies(tmp_path/'state', BASE, propose, None, ceiling, reserve, FILES, EVIDENCE, **options)


def row(report, strategy_id):
    return next(r for r in report['strategies'] if r['strategy']['id'] == strategy_id)


def test_comparison_selects_only_controller_verified_candidates_for_review(tmp_path, controller):
    report = compare(tmp_path, controller)
    assert report['evidence_source'] == 'controller_verified_development_only'
    assert [(r['strategy']['id'], r['disposition'], r['selected']) for r in report['strategies']] == [
        ('no-op', 'no_change', None),
        ('single-revision', 'development_recommended_for_review', 'p1'),
        ('reflective-gepa-inspired', 'development_recommended_for_review', 'p1')]
    attempts = row(report, 'reflective-gepa-inspired')['attempts']
    assert [(a['candidate_id'], a['metrics']) for a in attempts] == [
        ('base', dict(primary_effect=0)), ('p1', dict(primary_effect=.5)),
        ('p2', dict(primary_effect=.1)), ('p3', dict(primary_effect=.1))]
    assert report['finalists'] == [dict(strategy='single-revision', candidate_id='p1'),
                                   dict(strategy='reflective-gepa-inspired', candidate_id='p1')]
    assert report['advantage_claim'] is False
    assert all(r['measured_usage'] is None for r in report['strategies'])


def test_every_strategy_charges_the_same_ceiling_and_reports_actual_spend(tmp_path, controller):
    report = compare(tmp_path, controller, ceiling=12, reserve=2)
    assert {(r['charges']['ceiling'], r['charges']['reserve']) for r in report['strategies']} == {(12, 2)}
    spent = {r['strategy']['id']: r['charges']['spent'] for r in report['strategies']}
    assert spent['no-op'] < spent['single-revision'] < spent['reflective-gepa-inspired'] <= 12
    assert row(report, 'reflective-gepa-inspired')['charges']['by_kind']['development_confirmation'] == 1


def test_no_op_dispatches_only_the_baseline_and_keeps_the_duplicate_as_a_rejection(tmp_path, controller):
    report = compare(tmp_path, controller, strategies=[('no-op', 1)])
    assert controller.calls == [('base', 'baseline')]
    assert row(report, 'no-op')['rejections'] == [dict(index=1, candidate_id='base-noop', reason='duplicate_patch')]
    assert report['finalists'] == []


def test_invalid_proposals_are_reported_as_rejections_and_never_dispatched(tmp_path, controller):
    def propose(request):
        if request['index'] == 1:
            return CandidateRecord('escape', (), 'm', {'grader.py': 'x'}, ('e1',), 'h', 'f')
        raise RuntimeError('proposer crashed')
    report = compare(tmp_path, controller, propose=propose, strategies=[('reflective-gepa-inspired', 1)])
    assert [r['reason'] for r in row(report, 'reflective-gepa-inspired')['rejections']] == ['invalid_or_unauthorized_proposal'] * 3
    assert controller.calls == [('base', 'baseline')]


def test_unsafe_candidate_is_reported_but_never_selected(tmp_path, controller):
    controller.unsafe = {'p1'}
    report = compare(tmp_path, controller, strategies=[('single-revision', 1)])
    single = row(report, 'single-revision')
    assert [(a['candidate_id'], a['safe'], a['metrics']) for a in single['attempts']] == [
        ('base', True, dict(primary_effect=0)), ('p1', False, {})]
    assert (single['disposition'], single['selected'], report['finalists']) == ('no_change', None, [])


def test_failed_fresh_confirmation_leaves_no_finalist(tmp_path, controller):
    controller.unsafe = {'confirm'}
    report = compare(tmp_path, controller, strategies=[('single-revision', 1)])
    assert controller.calls[-1] == ('p1', 'development_confirmation')
    single = row(report, 'single-revision')
    assert (single['disposition'], single['selected'], report['finalists']) == ('rejected_or_inconclusive', None, [])


def test_smaller_shared_ceiling_stops_larger_pools_before_dispatch(tmp_path, controller):
    report = compare(tmp_path, controller, ceiling=4, reserve=1)
    assert row(report, 'reflective-gepa-inspired')['disposition'] == 'budget_exhausted'
    assert all(r['charges']['spent'] <= 3 for r in report['strategies'])
    assert {stage for _, stage in controller.calls} == {'baseline'}


def test_repeating_a_comparison_resumes_without_dispatching_again(tmp_path, controller):
    first = compare(tmp_path, controller)
    dispatched = list(controller.calls)
    again = compare(tmp_path, controller)
    assert controller.calls == dispatched
    assert all(r['resumed_without_execution'] for r in again['strategies'])
    assert [r['charges'] for r in again['strategies']] == [r['charges'] for r in first['strategies']]
    assert [r['disposition'] for r in again['strategies']] == [r['disposition'] for r in first['strategies']]


def test_resuming_under_a_different_budget_is_refused_without_dispatching(tmp_path, controller):
    compare(tmp_path, controller, ceiling=12)
    dispatched = list(controller.calls)
    with pytest.raises(ValueError, match='budget changed'):
        compare(tmp_path, controller, ceiling=11)
    assert controller.calls == dispatched


def test_interrupted_strategy_is_reported_interrupted_and_never_redispatched(tmp_path, controller):
    controller.interrupt_on = 'development'
    with pytest.raises(KeyboardInterrupt):
        compare(tmp_path, controller, strategies=[('single-revision', 1)])
    controller.interrupt_on = None
    dispatched = list(controller.calls)
    report = compare(tmp_path, controller, strategies=[('single-revision', 1)])
    single = row(report, 'single-revision')
    assert (single['disposition'], single['selected'], report['finalists']) == ('interrupted', None, [])
    assert controller.calls == dispatched


@pytest.mark.parametrize('evaluate, certify', [
    pytest.param(lambda c: dict(safe=True, metrics={}), None, id='raw-evaluate'),
    pytest.param(None, lambda c: dict(status='recommended'), id='raw-certify'),
])
def test_run_strategy_refuses_raw_callbacks_beside_the_controller(evaluate, certify, tmp_path, controller):
    with pytest.raises(ValueError, match='controller'):
        run_strategy('single-revision', 1, tmp_path/'state/s.json', BASE, proposer, evaluate, certify,
                     Budget(8, 1), FILES, EVIDENCE, controller=controller.context)
    assert controller.calls == []
    assert not (tmp_path/'state').exists()


def test_run_strategy_returns_the_guarded_result_with_its_strategy_identity(tmp_path, controller):
    result = run_strategy('single-revision', 1, tmp_path/'state/s.json', BASE, proposer, None, None, Budget(8, 1),
                          FILES, EVIDENCE, controller=controller.context)
    assert (result['status'], result['finalist'], result['strategy']['id']) == (
        'development_recommended_for_review', 'p1', 'single-revision')
    assert result['evidence_source'] == 'controller_verified_development_only'
    assert (result['automatic_promotion'], result['publication_allowed'], result['adoption_authority']) == (False,) * 3


def test_wrong_controller_type_is_refused_before_any_state_exists(tmp_path, controller):
    with pytest.raises(ValueError, match='actual history'):
        compare(tmp_path, controller, controller=object())
    assert not (tmp_path/'state').exists() or list((tmp_path/'state').iterdir()) == []


def test_a_deadline_without_a_controller_is_refused_not_silently_ignored(tmp_path):
    with pytest.raises(ValueError, match='deadline'):
        run_strategy('no-op', 1, tmp_path/'s.json', BASE, proposer, lambda c: dict(safe=True, metrics={}),
                     lambda c: dict(status='recommended'), Budget(8, 1), FILES, EVIDENCE, parent_deadline=10)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('evaluate, certify', [
    pytest.param(None, lambda c: dict(status='recommended'), id='no-evaluate'),
    pytest.param(lambda c: dict(safe=True, metrics={}), None, id='no-certify'),
    pytest.param(None, None, id='neither'),
])
def test_callback_route_without_both_callbacks_is_refused_before_any_state_exists(evaluate, certify, tmp_path):
    with pytest.raises(ValueError, match='callback route'):
        run_strategy('single-revision', 1, tmp_path/'s.json', BASE, proposer, evaluate, certify, Budget(8, 1), FILES, EVIDENCE)
    assert list(tmp_path.iterdir()) == []


SINGLE = [('single-revision', 1)]


def retried(tmp_path, fake, strategies=SINGLE, **options):
    report = compare(tmp_path, fake, strategies=strategies, retry_comparator=True, **options)
    return report, row(report, strategies[0][0])['retry_comparator']


@pytest.mark.parametrize('strategy, runs', [
    pytest.param(('single-revision', 1), 2, id='single-revision'),
    pytest.param(('reflective-gepa-inspired', 1), 4, id='reflective'),
])
def test_unchanged_baseline_is_dispatched_once_per_non_final_controller_run_the_search_made(strategy, runs, tmp_path, controller):
    report, retry = retried(tmp_path, controller, [strategy])
    searched = [call for call in controller.calls if call[1] != 'baseline_retry']
    assert sum(stage in ('baseline', 'development') for _, stage in searched) == runs
    assert retry['matched_stage_units'] == runs
    assert controller.calls[len(searched):] == [('base', 'baseline_retry')] * runs
    assert [a['candidate_id'] for a in retry['attempts']] == [f'base-retry-{i}' for i in range(1, runs + 1)]
    assert retry['status'] == 'complete'


def test_guarded_retries_spend_their_own_budget_under_the_same_ceiling_and_reserve(tmp_path, controller):
    _, retry = retried(tmp_path, controller, ceiling=12, reserve=2)
    assert retry['charges'] == dict(ceiling=12, reserve=2, spent=2, by_kind=dict(retry_trial=2))


@pytest.mark.parametrize('retry_effects, unqualified, expected', [
    pytest.param({}, set(), True, id='unchanged-baseline-stays-below-the-candidate'),
    pytest.param({1: .2, 2: .3}, set(), True, id='small-observed-noise-stays-below'),
    pytest.param({1: .5}, set(), False, id='a-retry-ties-the-candidate'),
    pytest.param({2: .9}, set(), False, id='one-lucky-retry-beats-the-candidate'),
    pytest.param({}, {1}, None, id='one-unqualified-retry-stays-unknown'),
    pytest.param({}, {1, 2}, None, id='no-qualified-retry-stays-unknown'),
])
def test_guarded_candidate_exceeds_the_retries_only_when_it_beats_every_qualified_one(
        retry_effects, unqualified, expected, tmp_path, controller):
    controller.retry_effects, controller.unqualified_retries = retry_effects, unqualified
    report, retry = retried(tmp_path, controller)
    assert retry['exceeds_best_retry'] is expected
    assert report['advantage_claim'] is False


def test_guarded_retries_keep_their_own_run_and_receipt_joins_and_label_the_controller_estimand(tmp_path, controller):
    _, retry = retried(tmp_path, controller)
    search_runs = {hashlib.sha256(str(number).encode()).hexdigest() for number in (1, 2, 3)}  # baseline, p1, confirmation
    retry_runs = {a['run_id'] for a in retry['attempts']}
    assert all(set(a) == {'candidate_id', 'digest', 'safe', 'metrics', 'run_id', 'receipt_sha256'} for a in retry['attempts'])
    assert len(retry_runs) == 2 and not search_runs & retry_runs
    assert [a['metrics'] for a in retry['attempts']] == [dict(primary_effect=0)] * 2
    assert 'controller-verified' in retry['estimand'] and 'unchanged baseline' in retry['estimand']


def test_guarded_retry_state_is_private_and_sits_beside_the_strategy_state(tmp_path, controller):
    retried(tmp_path, controller)
    saved = sorted(path.name for path in (tmp_path/'state').glob('retry-*.json'))
    assert len(saved) == 1 and saved[0].startswith('retry-single-revision-') and saved[0].endswith('-k2.json')
    assert ((tmp_path/'state'/saved[0]).stat().st_mode & 0o777) == 0o600


@pytest.mark.parametrize('strategies, unsafe, why', [
    pytest.param([('no-op', 1)], set(), 'no-op-selects-nothing', id='no-op'),
    pytest.param(SINGLE, {'confirm'}, 'failed-confirmation', id='failed-confirmation'),
    pytest.param(SINGLE, {'p1'}, 'unsafe-candidate', id='unsafe-candidate'),
])
def test_strategies_without_a_recommended_finalist_run_no_guarded_retries(strategies, unsafe, why, tmp_path, controller):
    controller.unsafe = unsafe
    report = compare(tmp_path, controller, strategies=strategies, retry_comparator=True)
    assert [r['retry_comparator'] for r in report['strategies']] == [None]
    assert all(stage != 'baseline_retry' for _, stage in controller.calls)
    assert not list((tmp_path/'state').glob('retry-*'))


def test_repeating_a_guarded_comparison_reverifies_retries_without_dispatching_them_again(tmp_path, controller):
    _, first = retried(tmp_path, controller)
    dispatched = list(controller.calls)
    _, again = retried(tmp_path, controller)
    assert controller.calls == dispatched
    assert again['resumed_without_execution'] is True
    assert (again['status'], again['attempts'], again['exceeds_best_retry']) == (
        first['status'], first['attempts'], first['exceeds_best_retry'])


def test_a_revoked_retry_receipt_blocks_the_cached_comparison_instead_of_trusting_it(tmp_path, controller):
    retried(tmp_path, controller)
    controller.revoke_retries = True
    _, again = retried(tmp_path, controller)
    assert (again['status'], again['exceeds_best_retry']) == ('blocked', None)
    assert [stage for _, stage in controller.calls].count('baseline_retry') == 2


def test_interrupted_guarded_retry_is_not_redispatched_and_leaves_the_comparison_unknown(tmp_path, controller):
    controller.interrupt_on = 'baseline_retry'
    with pytest.raises(KeyboardInterrupt):
        retried(tmp_path, controller)
    controller.interrupt_on = None
    dispatched = list(controller.calls)
    _, retry = retried(tmp_path, controller)
    assert controller.calls == dispatched
    assert (retry['status'], retry['exceeds_best_retry'], retry['attempts']) == ('interrupted', None, [])
    assert retry['charges']['by_kind'] == dict(retry_trial=1)


def test_a_failed_guarded_retry_blocks_the_comparison_and_is_not_redispatched(tmp_path, controller):
    controller.crash_on = 'baseline_retry'
    _, retry = retried(tmp_path, controller)
    assert (retry['status'], retry['exceeds_best_retry'], retry['attempts']) == ('blocked', None, [])
    controller.crash_on = None
    dispatched = list(controller.calls)
    _, again = retried(tmp_path, controller)
    assert controller.calls == dispatched
    assert (again['status'], again['exceeds_best_retry']) == ('blocked', None)


def test_a_baseline_changed_during_a_guarded_retry_blocks_the_comparison(tmp_path, controller):
    base = CandidateRecord('base', (), 'm', {}, ('e1',), 'hypothesis', 'falsifier')
    original = controller.evaluate

    def mutating(value, *, stage, **options):
        result = original(value, stage=stage, **options)
        if stage == 'baseline_retry':
            value.changes['AGENTS.md'] = 'sneaked in'
        return result
    controller.evaluate = mutating
    report = compare_strategies(tmp_path/'state', base, proposer, None, 12, 1, FILES, EVIDENCE, strategies=SINGLE,
                                controller=controller.context, retry_comparator=True)
    retry = row(report, 'single-revision')['retry_comparator']
    assert (retry['status'], retry['attempts'], retry['exceeds_best_retry']) == ('blocked', [], None)


def predicted(request):
    c = candidate(f'p{request["index"]}', f'text {request["index"]}')
    if request['index'] == 2:
        return c  # No prediction: this proposal must never reach the controller.
    return c, PredictionRecord(prediction_id=f'pred-{c.candidate_id}', mechanism_id='m', baseline_hash=BASE.digest,
        candidate_hash=c.digest, bank_version='bank1', grader_version='g1', kind='deterministic_boundary',
        observable_effect='recovers on the development task', competing_explanation='grader defect',
        falsifier='the development task still fails', criterion='fail_to_pass',
        development_task_ids=('dev',), evidence_refs=('e1',))


def test_guarded_route_freezes_each_prediction_before_dispatch_and_never_dispatches_an_unpredicted_proposal(tmp_path, controller):
    predictions, frozen_at_dispatch = strategies.PredictionGate(PredictionLedger(), 'bank1', 'g1'), {}
    dispatch = controller.evaluate

    def recording(value, **kwargs):
        frozen_at_dispatch[value.candidate_id] = [r.candidate_hash for r, _ in predictions.ledger.entries()]
        return dispatch(value, **kwargs)
    controller.evaluate = recording
    report = compare(tmp_path, controller, propose=predicted, strategies=[('reflective-gepa-inspired', 1)],
                     predictions=predictions)
    reflective = row(report, 'reflective-gepa-inspired')
    assert [(r['index'], r['prediction_id']) for r in reflective['predictions']['receipts']] == [(1, 'pred-p1'), (3, 'pred-p3')]
    assert [(r['index'], r['reason']) for r in reflective['predictions']['rejected']] == [(2, 'missing_prediction')]
    assert [name for name, _ in controller.calls if name == 'p2'] == []
    assert candidate('p1', 'text 1').digest in frozen_at_dispatch['p1']
    assert candidate('p3', 'text 3').digest in frozen_at_dispatch['p3']
    assert [r['reason'] for r in reflective['rejections']] == ['invalid_or_unauthorized_proposal']
