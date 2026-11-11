"""Strategy registry and built-ins through the shared search contracts, using fake proposers."""
import json

import pytest

from agent_harness_lab.optimization import strategies
from agent_harness_lab.optimization.budget import Budget
from agent_harness_lab.optimization.search import CandidateRecord
from agent_harness_lab.optimization.strategies import get, register, run_strategy

EVIDENCE = [dict(scope='development', ref='e1')]
FILES = ['AGENTS.md']


def candidate(name, content=None):
    return CandidateRecord(name, (), 'm', {} if content is None else {'AGENTS.md': content},
                           ('e1',), 'hypothesis', 'falsifier')


def run(strategy, tmp_path, proposer, evaluate=None, budget=None, evidence=EVIDENCE, **options):
    evaluate = evaluate or (lambda c: dict(safe=True, metrics=dict(q=1 if c.changes else 0)))
    return run_strategy(strategy, 1, tmp_path/'s.json', candidate('base'), proposer, evaluate,
                        lambda c: dict(status='recommended'), budget or Budget(10, 1), FILES, evidence, **options)


def test_registry_is_explicit_and_versioned():
    assert [k for k in strategies.registered() if k[0] == 'no-op'] == [('no-op', 1)]
    for bad in (('missing', 1), ('no-op', 2), ('os.system', 1)):
        with pytest.raises(ValueError):
            get(*bad)
    with pytest.raises(ValueError):
        register(strategies.NO_OP)
    with pytest.raises(ValueError):
        register(dict(strategy_id='x', version=1))


@pytest.mark.parametrize('options', [dict(seed='1'), dict(variants=0), dict(variants=9), dict(extra=1), dict(variants=True)])
def test_malformed_config_rejected(options, tmp_path):
    with pytest.raises(ValueError):
        run('single-revision', tmp_path, lambda r: None, **options)


def test_no_op_can_remain_selected_and_rejected_proposal_is_retained(tmp_path):
    result = run('no-op', tmp_path, lambda r: pytest.fail('no-op never calls a proposer'))
    assert result['status'] == 'no_change'
    assert result['rejections'] == [dict(candidate_id='base-noop', reason='duplicate_patch')]
    assert result['automatic_promotion'] is False


def test_single_revision_uses_one_slot_and_reflective_includes_simplification(tmp_path):
    seen = []
    def proposer(request):
        seen.append((request['kind'], request['seed']))
        return candidate(f'p{request["index"]}', f'text {request["index"]}')
    single = run('single-revision', tmp_path/'a', proposer)
    assert [k for k, _ in seen] == ['revise'] and single['strategy']['config']['variants'] == 1
    seen.clear()
    result = run('reflective-gepa-inspired', tmp_path/'b', proposer, seed=7)
    assert [k for k, _ in seen] == ['add', 'add', 'simplify']
    first = list(seen)
    seen.clear()
    run('reflective-gepa-inspired', tmp_path/'c', proposer, seed=7)
    assert seen == first  # fixed seed reproduces slot order and derived seeds
    assert single['status'] == 'recommended' and result['status'] == 'no_change'  # three-way tie keeps the baseline
    assert len(result['candidates']) == 4


def test_bad_proposals_are_rejected_before_selection(tmp_path):
    def proposer(request):
        i = request['index']
        if i == 1:
            return CandidateRecord('escape', (), 'm', {'grader.py': 'x'}, ('e1',), 'h', 'f')
        if i == 2:
            raise RuntimeError('proposer crashed')
        return candidate('dup', 'text')
    seen = []
    def evaluate(c):
        seen.append(c.candidate_id)
        return dict(safe=True, metrics=dict(q=0))
    result = run('reflective-gepa-inspired', tmp_path, proposer, evaluate)
    assert seen == ['base', 'dup']
    assert len(result['rejections']) == 2 and result['status'] == 'no_change'


def test_mutated_candidate_during_evaluation_is_rejected(tmp_path):
    def proposer(request):
        return candidate('p', 'text')
    def evaluate(c):
        if c.candidate_id == 'p':
            object.__setattr__(c, 'changes', {'AGENTS.md': 'tampered'})
        return dict(safe=True, metrics=dict(q=1))
    result = run('single-revision', tmp_path, proposer, evaluate)
    assert result['rejections'][0]['reason'] == 'candidate_mutated_during_evaluation'
    assert result['status'] == 'no_change'


@pytest.mark.parametrize('evidence', [[dict(scope='final', ref='x')], [dict(ref='x')], 'all', [EVIDENCE[0], 'row']])
def test_final_or_unscoped_evidence_is_refused(evidence, tmp_path):
    with pytest.raises(ValueError):
        run('single-revision', tmp_path, lambda r: None, evidence=evidence)
    assert not (tmp_path/'s.json').exists()


def test_budget_ceiling_holds_and_resume_does_not_repeat_charged_work(tmp_path):
    calls = []
    def evaluate(c):
        calls.append(c.candidate_id)
        return dict(safe=True, metrics=dict(q=0))
    proposer = lambda r: candidate(f'p{r["index"]}', f'text {r["index"]}')
    budget = Budget(3, 1)
    result = run('reflective-gepa-inspired', tmp_path, proposer, evaluate, budget)
    assert result['status'] == 'budget_exhausted' and budget.spent <= 2
    again = run('reflective-gepa-inspired', tmp_path, proposer, evaluate, Budget(3, 1))
    assert again['resumed_without_execution'] and calls == ['base']


def test_changed_configuration_is_a_new_run_identity(tmp_path):
    proposer = lambda r: candidate(f'p{r["index"]}', f'text {r["index"]}')
    one = run('reflective-gepa-inspired', tmp_path, proposer, seed=1)
    two = run('reflective-gepa-inspired', tmp_path, proposer, seed=2)
    assert one['strategy']['identity'] != two['strategy']['identity']
    assert one['strategy']['state_path'] != two['strategy']['state_path']
    assert not two.get('resumed_without_execution')
    json.dumps(one['strategy'])
