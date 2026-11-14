"""Registered strategies through the guarded controller route; the controller core is a synthetic double."""
# fixed-in: 4e7d4a62
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from unittest.mock import patch

import pytest

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
        binding = dict(schema_version=1, template_sha256=self.template['template_sha256'],
            candidate_record=json.loads(encoded(asdict(value))), patch_sha256=value.digest,
            harness_sha256='b'*64, tree_sha256='d'*64, stage=stage, event_id=event_id,
            attempt_id=hashlib.sha256(('attempt'+ident).encode()).hexdigest(), run_id=ident,
            receipt_sha256='e'*64, plan_sha256='f'*64, manifest_sha256='1'*64)
        self.saved[ident] = deepcopy(binding)
        return dict(binding=binding, safe=True, metrics={'invented_effect': 999})  # Unverified labels must be ignored.

    def verify(self, binding):
        assert self.saved.get(binding['run_id']) == binding
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
