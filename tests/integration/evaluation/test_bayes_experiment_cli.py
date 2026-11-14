"""`ahl experiment --records` reports the bayesian_decision block for a declared plan and nothing new otherwise.

Synthetic banks only (tests/support/bayes_records.py); every case is offline and recommendation-only.
"""
from dataclasses import asdict, replace
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_harness_lab.evaluation import statistics
from agent_harness_lab.evaluation.statistics import analyze
from agent_harness_lab.promotion import policy
from agent_harness_lab.promotion.policy import decide
from tests.support.bayes_records import GOLDEN_KINDS, KINDS, bank, expected_verdicts, experiment, golden_case, run

GOLDEN = Path(__file__).resolve().parents[2] / 'support' / 'golden_profiles' / 'ahl-experiment-analysis-v1.json'
AUTHORITY = ('recommendation_authority', 'automatic_promotion', 'publication_allowed')


def reported(tmp_path, case):
    code, out, err = experiment(tmp_path, case['spec'], case['records'])
    assert err == '' and code == 3, (code, err)  # a recorded descriptive analysis always exits 3
    return json.loads(out)


@pytest.mark.parametrize('kind', KINDS)
def test_each_named_fixture_reaches_its_verdict_through_the_command(tmp_path, kind):
    result = reported(tmp_path, bank(kind))
    block = result['bayesian_decision']
    row = expected_verdicts()[kind]  # committed in examples/bayes-decision/expected-verdicts.json
    assert (block['verdict'], block['reason'], block['stage']) == (row['verdict'], row['reason'], row['stage'])
    assert all(block[name] is False for name in AUTHORITY) and result['promotion']['automatic'] is False
    assert block['oracle_validity'] == result['oracle_validity'] == 'unverified'
    assert result['promotion']['status'] != 'recommended'


def test_the_block_carries_the_fields_of_the_design_output_block(tmp_path):
    block = reported(tmp_path, bank('clear_win'))['bayesian_decision']
    assert {'profile', 'plan_sha256', 'parameters_sha256', 'verdict', 'reason', 'stage', 'score', 'draws', 'seed_source',
            'monte_carlo_se', 'roots', 'unknown_arms', 'bound_resolution', 'families', 'cross_check', 'resource_gate',
            'prior_sensitivity', 'gate', 'floors', 'critical_regression', *AUTHORITY} <= set(block)
    assert block['plan_sha256'] == bank('clear_win')['spec'].digest and set(block['families']) == {'alpha', 'beta'}


@pytest.mark.parametrize('kind', GOLDEN_KINDS)
def test_a_plan_without_the_declaration_prints_the_byte_identical_pre_change_output(tmp_path, kind):
    stored = json.loads(GOLDEN.read_text())[kind]
    code, out, err = experiment(tmp_path, *[golden_case(kind)[k] for k in ('spec', 'records')])
    assert (code, err, out) == (stored['exit_code'], '', stored['stdout'])
    assert 'bayesian_decision' not in json.loads(out)


@pytest.mark.parametrize('kind', GOLDEN_KINDS)
def test_the_hoeffding_analysis_and_promotion_of_a_declared_plan_are_unchanged(tmp_path, kind):
    case = golden_case(kind, declared=True)
    result = reported(tmp_path, case)
    block = result.pop('bayesian_decision')
    assert result.pop('descriptive_report')['descriptive_only'] is True  # a sibling key, also outside the unchanged analysis
    plain = json.loads(json.loads(GOLDEN.read_text())[kind]['stdout'])
    assert result == plain and block['plan_sha256'] == case['spec'].digest


def test_a_better_verdict_beside_an_inconclusive_hoeffding_analysis_never_recommends(tmp_path):
    result = reported(tmp_path, bank('clear_win'))
    assert result['bayesian_decision']['verdict'] == 'better' and result['status'] == 'inconclusive'
    assert result['promotion']['status'] != 'recommended' and result['promotion']['automatic'] is False


def test_the_promotion_policy_gives_the_same_decision_with_and_without_the_block():
    case = bank('clear_win')
    spec = case['spec']
    plain = analyze(spec, case['records'])
    with_block = dict(plain, bayesian_decision=dict(verdict='better', recommendation_authority=False, automatic_promotion=False))
    assert asdict(decide(spec, plain)) == asdict(decide(spec, with_block))


def test_a_better_block_cannot_make_an_unsupported_hoeffding_analysis_recommended(monkeypatch):
    case = bank('clear_win')
    spec = replace(case['spec'], profile='certification', partition='certification')
    block = dict(verdict='better', recommendation_authority=False, automatic_promotion=False)
    monkeypatch.setattr(policy, 'isolation_qualification', lambda scope=None: 'qualified')
    ready = dict(effective_runtime_verified=True, graders_audited=True, lineage_reviewed=True, certification_reserved=True)
    for hoeffding, expected in (('inconclusive', 'inconclusive'), ('supported', 'recommended')):
        analysis = dict(status=hoeffding, reason='synthetic', oracle_validity='current', bayesian_decision=block)
        decision = decide(spec, analysis, **ready)
        assert (decision.status, decision.automatic) == (expected, False)
        assert asdict(decision) == asdict(decide(spec, {k: v for k, v in analysis.items() if k != 'bayesian_decision'}, **ready))


def test_a_pinned_resource_contract_without_a_resource_decision_withholds_better_through_the_command(tmp_path):
    block = reported(tmp_path, bank('clear_win', resource_contract_sha256='a' * 64))['bayesian_decision']
    assert (block['verdict'], block['reason'], block['stage']) == ('insufficient_evidence', 'resource_gate_not_evaluated', 'resource')
    assert block['resource_gate'] == dict(status='not_evaluated')


def test_a_disagreeing_cross_check_downgrades_better_through_the_command(tmp_path):
    block = reported(tmp_path, bank('clear_win', bayes={'cross_check_alpha': 1e-6}))['bayesian_decision']
    assert (block['verdict'], block['reason']) == ('insufficient_evidence', 'cross_check_disagrees')
    assert block['cross_check']['agrees'] is False and block['cross_check']['method'] == 'monte_carlo'


def test_missing_records_block_the_block_while_the_fixed_analysis_stays_incomplete(tmp_path):
    case = bank('clear_win')
    case['records'] = case['records'][:-3] + case['records'][-3:-2] * 2  # one duplicate, two missing
    result = reported(tmp_path, case)
    assert result['status'] == 'incomplete' and result['reason'] == 'missing_or_duplicate_trials'
    assert result['bayesian_decision']['verdict'] == 'blocked' and result['bayesian_decision']['score'] is None


def test_the_block_is_not_computed_for_other_modes(tmp_path):
    case = bank('clear_win')
    (tmp_path / 'plan.json').write_text(json.dumps(case['spec'].to_dict()))
    code, out, err = run('experiment', '--spec', str(tmp_path / 'plan.json'), '--preflight')
    assert err == '' and 'bayesian_decision' not in json.loads(out)


def test_the_block_never_reruns_the_hoeffding_engine(tmp_path):
    original, calls = statistics.analyze, []

    def spy(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    with patch('agent_harness_lab.evaluation.statistics.analyze', spy):
        reported(tmp_path, bank('clear_win'))
    assert len(calls) == 1
