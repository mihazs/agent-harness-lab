"""Strategy proposals must arrive with a prediction frozen before the candidate is scored (#12, #10)."""
# fixed-in: 0c6f6e40
from dataclasses import replace

import pytest

from agent_harness_lab.diagnostics.predictions import PredictionLedger, PredictionRecord
from agent_harness_lab.optimization import strategies
from agent_harness_lab.optimization.budget import Budget
from agent_harness_lab.optimization.search import CandidateRecord
from agent_harness_lab.optimization.strategies import compare_strategies, run_strategy

EVIDENCE = [dict(scope='development', ref='e1')]
FILES = ['AGENTS.md']
BASE = CandidateRecord('base', (), 'm', {}, ('e1',), 'hypothesis', 'falsifier')


def candidate(name, content, **changes):
    return replace(CandidateRecord(name, (), 'm', {'AGENTS.md': content}, ('e1',), 'hypothesis', 'falsifier'), **changes)


def prediction(c, **changes):
    base = dict(prediction_id=f'pred-{c.candidate_id}', mechanism_id=c.mechanism, baseline_hash=BASE.digest,
                candidate_hash=c.digest, bank_version='bank1', grader_version='g1', kind='deterministic_boundary',
                observable_effect='recovers on the development task', competing_explanation='grader defect',
                falsifier='the development task still fails', criterion='fail_to_pass',
                development_task_ids=('dev',), evidence_refs=('e1',))
    base.update(changes)
    return PredictionRecord(**base)


def gate(**changes):
    return strategies.PredictionGate(PredictionLedger(), 'bank1', 'g1', **changes)


def proposer(request):
    c = candidate(f'p{request["index"]}', f'text {request["index"]}')
    return c, prediction(c)


def evaluate(c):
    return dict(safe=True, metrics=dict(q=0))


def run(tmp_path, predictions, propose=proposer, strategy='reflective-gepa-inspired', ev=evaluate, **options):
    return run_strategy(strategy, 1, tmp_path/'s.json', BASE, propose, ev, lambda c: dict(status='recommended'),
                        Budget(12, 1), FILES, EVIDENCE, predictions=predictions, **options)


def test_prediction_is_frozen_in_the_ledger_before_its_candidate_is_evaluated(tmp_path):
    predictions, frozen_at_evaluation = gate(), {}

    def checked(c):
        frozen_at_evaluation[c.candidate_id] = [r.candidate_hash for r, _ in predictions.ledger.entries()]
        return evaluate(c)
    run(tmp_path, predictions, ev=checked)
    assert 'base' in frozen_at_evaluation
    for name in ('p1', 'p2', 'p3'):
        assert candidate(name, f'text {name[1:]}').digest in frozen_at_evaluation[name]


def test_result_reports_one_preregistered_receipt_per_frozen_prediction(tmp_path):
    predictions = gate()
    report = run(tmp_path, predictions)['predictions']
    assert [(r['index'], r['candidate_id'], r['prediction_id']) for r in report['receipts']] == [
        (1, 'p1', 'pred-p1'), (2, 'p2', 'pred-p2'), (3, 'p3', 'pred-p3')]
    assert all(r['preregistered'] is True for r in report['receipts'])
    assert [r['receipt_id'] for r in report['receipts']] == [r['receipt_id'] for r in predictions.ledger.receipts]
    assert report['rejected'] == [] and report['ledger_verified'] is True


def bare(request):
    return candidate(f'p{request["index"]}', f'text {request["index"]}')


def mismatched(**changes):
    def propose(request):
        c = candidate(f'p{request["index"]}', f'text {request["index"]}')
        return c, prediction(c, **changes)
    return propose


def raises(request):
    raise RuntimeError('proposer crashed')


def unauthorized(request):
    c = candidate(f'p{request["index"]}', 'x')
    return replace(c, changes={'grader.py': 'x'}), prediction(c)


@pytest.mark.parametrize('propose, reason', [
    pytest.param(lambda r: None, 'no_proposal', id='proposer-declined'),
    pytest.param(raises, 'proposal_failed', id='proposer-raised'),
    pytest.param(bare, 'missing_prediction', id='candidate-without-prediction'),
    pytest.param(lambda r: (bare(r), 'prediction'), 'missing_prediction', id='prediction-is-not-a-record'),
    pytest.param(unauthorized, 'invalid_or_unauthorized_proposal', id='patch-outside-the-authorized-files'),
    pytest.param(mismatched(candidate_hash='0'*64), 'prediction_identity_mismatch', id='prediction-names-another-candidate'),
    pytest.param(mismatched(mechanism_id='other'), 'prediction_identity_mismatch', id='prediction-names-another-mechanism'),
    pytest.param(mismatched(baseline_hash='0'*64), 'prediction_identity_mismatch', id='prediction-names-another-baseline'),
    pytest.param(mismatched(bank_version='bank2'), 'prediction_identity_mismatch', id='prediction-names-another-bank'),
    pytest.param(mismatched(grader_version='g2'), 'prediction_identity_mismatch', id='prediction-names-another-grader'),
    pytest.param(mismatched(falsifier=' '), 'invalid_prediction', id='prediction-without-a-falsifier'),
    pytest.param(mismatched(development_task_ids=()), 'invalid_prediction', id='prediction-without-development-tasks'),
    pytest.param(mismatched(development_task_ids=('hidden',)), 'invalid_prediction', id='prediction-names-a-protected-task'),
    pytest.param(mismatched(evidence_refs=('final-feedback',)), 'invalid_prediction', id='prediction-cites-evidence-outside-the-allowlist'),
])
def test_a_proposal_without_a_valid_frozen_prediction_is_rejected_before_it_is_scored(tmp_path, propose, reason):
    predictions, scored = gate(protected_task_ids=('hidden',), allowed_refs=('e1',)), []

    def recording(c):
        scored.append(c.candidate_id)
        return evaluate(c)
    result = run(tmp_path, predictions, propose, ev=recording)
    assert scored == ['base']
    assert [r['reason'] for r in result['predictions']['rejected']] == [reason]*3
    assert result['predictions']['receipts'] == [] and predictions.ledger.entries() == []
    assert result['status'] == 'no_change'
    assert [c['candidate_id'] for c in result['candidates']] == ['base']


def test_a_prediction_id_reused_for_a_different_claim_is_rejected(tmp_path):
    def propose(request):
        c = candidate(f'p{request["index"]}', f'text {request["index"]}')
        return c, prediction(c, prediction_id='same-id')
    result = run(tmp_path, gate(), propose)
    assert [(r['index'], r['reason']) for r in result['predictions']['rejected']] == [
        (2, 'invalid_prediction'), (3, 'invalid_prediction')]
    assert [r['index'] for r in result['predictions']['receipts']] == [1]


def test_no_op_needs_no_prediction_and_freezes_none(tmp_path):
    predictions = gate()
    result = run(tmp_path, predictions, lambda r: pytest.fail('no-op never calls a proposer'), strategy='no-op')
    assert result['predictions']['receipts'] == [] and result['predictions']['rejected'] == []
    assert predictions.ledger.entries() == []


def test_a_resumed_run_reports_its_prediction_receipts_as_unknown_not_empty(tmp_path):
    run(tmp_path, gate())
    again = run(tmp_path, gate(), lambda r: pytest.fail('completed work is never proposed again'))
    assert again['resumed_without_execution'] is True
    assert again['predictions']['receipts'] is None and again['predictions']['rejected'] is None


@pytest.mark.parametrize('malformed', [
    pytest.param(lambda: object(), id='not-a-gate'),
    pytest.param(lambda: strategies.PredictionGate(object(), 'bank1', 'g1'), id='ledger-is-not-a-prediction-ledger'),
])
def test_a_malformed_gate_is_refused_before_any_state_exists(tmp_path, malformed):
    with pytest.raises(ValueError, match='prediction gate'):
        run(tmp_path, malformed())
    assert list(tmp_path.iterdir()) == []


def test_without_a_gate_a_bare_candidate_runs_and_the_result_has_no_prediction_report(tmp_path):
    result = run(tmp_path, None, bare)
    assert 'predictions' not in result and len(result['candidates']) == 4


def test_the_proposal_request_names_its_strategy_so_prediction_ids_can_be_namespaced(tmp_path):
    seen = []

    def propose(request):
        seen.append(request['strategy'])
        return bare(request)
    run(tmp_path, None, propose, strategy='single-revision')
    assert seen == ['single-revision']


def test_comparison_shares_one_ledger_and_reports_each_strategys_receipts(tmp_path):
    predictions = gate()

    def propose(request):
        c = candidate(f'p{request["index"]}', f'text {request["index"]}')
        return c, prediction(c, prediction_id=f'{request["strategy"]}-{c.candidate_id}')
    report = compare_strategies(tmp_path, BASE, propose, evaluate, 12, 1, FILES, EVIDENCE, predictions=predictions)
    receipts = {r['strategy']['id']: [x['prediction_id'] for x in r['predictions']['receipts']] for r in report['strategies']}
    assert receipts == {'no-op': [], 'single-revision': ['single-revision-p1'],
                        'reflective-gepa-inspired': [f'reflective-gepa-inspired-p{i}' for i in (1, 2, 3)]}
    assert len(predictions.ledger.entries()) == 4 and predictions.ledger.verify()


def test_comparison_refuses_a_malformed_gate_before_any_state_exists(tmp_path):
    with pytest.raises(ValueError, match='prediction gate'):
        compare_strategies(tmp_path/'state', BASE, bare, evaluate, 12, 1, FILES, EVIDENCE, predictions='gate')
    assert not (tmp_path/'state').exists()
