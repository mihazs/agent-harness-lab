"""The history-store analysis that `ahl improve` carries, its decision receipt and the saved analysis report the block.

Synthetic controller runs and synthetic banks only; nothing here dispatches a worker, a model or the network.
"""
from contextlib import ExitStack
import json
from unittest.mock import patch

import pytest

from agent_harness_lab.evaluation.bayes_analysis import attach
from agent_harness_lab.evaluation.decision_receipts import hashed
from agent_harness_lab.evaluation.oracle_history import HistoryStore
from agent_harness_lab.evaluation.statistics import analyze
from agent_harness_lab.private_journal import encoded
from tests.support.bayes_records import bank
from tests.support.preservation_receipts import PreservationReceipt

AUTHORITY = ('recommendation_authority', 'automatic_promotion', 'publication_allowed')


@pytest.fixture
def host():
    with ExitStack() as cleanups:
        yield PreservationReceipt(cleanups)


def analyzed(tmp_path, case, validity='current', *, inputs=None, result=None):
    store = HistoryStore(tmp_path / 'history')
    checked = (tmp_path, [], inputs or dict(schema_version=1, run_id='synthetic'), case['spec'], {} if result is None else result,
               validity)
    with patch.object(store, 'trials', return_value=case['records']):
        return store._analyze_checked(checked)


def test_a_declared_plan_adds_the_block_beside_the_unchanged_fixed_analysis(tmp_path):
    case, plain = bank('clear_win'), bank('clear_win', bayes=False)
    result, baseline = analyzed(tmp_path, case), analyzed(tmp_path, plain)
    block = result.pop('bayesian_decision')
    assert result.pop('descriptive_report')['descriptive_only'] is True  # a sibling key, also outside the unchanged analysis
    assert (block['verdict'], block['reason']) == ('better', 'all_gate_stages_passed')
    assert block['oracle_validity'] == 'current' and all(block[name] is False for name in AUTHORITY)
    assert result == baseline and 'bayesian_decision' not in baseline
    assert result['automatic_promotion'] is False and result['status'] == 'inconclusive'


@pytest.mark.parametrize('validity,reason', [('invalidated', 'oracle_quarantined'), ('incomplete', 'controller_run_not_sealed')])
def test_an_oracle_that_is_not_current_blocks_the_block(tmp_path, validity, reason):
    result = analyzed(tmp_path, bank('clear_win'), validity)
    block = result['bayesian_decision']
    assert (block['verdict'], block['reason'], block['stage']) == ('blocked', reason, 'validity')
    assert block['score'] is None and block['oracle_validity'] == validity and result['reason'] == reason


@pytest.mark.parametrize('effects,verdict,reason', [
    (dict(critical_candidate_effect=True, required_observation_unknown=False), 'worse', 'critical_regression'),
    (dict(critical_candidate_effect=False, required_observation_unknown=True), 'blocked', 'behavior_observation_incomplete')])
def test_observed_behavior_effects_reach_the_block_as_the_existing_veto_and_block(tmp_path, effects, verdict, reason):
    inputs = dict(schema_version=1, run_id='synthetic', behavior_policies={'task': {}})
    with patch('agent_harness_lab.evaluation.behavior.report', return_value=effects):
        result = analyzed(tmp_path, bank('clear_win'), inputs=inputs, result={'trials': []})
    block = result['bayesian_decision']
    assert (block['verdict'], block['reason']) == (verdict, reason) and result['reason'] == reason


def test_the_decision_receipt_of_a_sealed_run_carries_the_block_and_binds_it(host):
    # A sealed run of a Bayesian plan needs enough roots to pass the Hoeffding precision preflight, which the offline
    # controller fixture does not have; the block is supplied by the store's own analysis step instead.
    case = bank('clear_win')
    block = attach(case['spec'], case['records'], analyze(case['spec'], case['records']))['bayesian_decision']
    original = HistoryStore._analyze_checked
    fixture = host.fixture()
    with patch.object(HistoryStore, '_analyze_checked', lambda self, checked: dict(original(self, checked), bayesian_decision=block)):
        summary, value = host.receipt(fixture)
        payload = value['receipt']
        assert value['analysis']['bayesian_decision'] == block
        assert payload['analysis_disposition']['bayesian_decision'] == dict(
            verdict=block['verdict'], reason=block['reason'], stage=block['stage'], parameters_sha256=block['parameters_sha256'])
        assert payload['analysis_sha256'] == hashed(encoded(value['analysis']))  # the whole block is bound, not only the summary
        assert payload['automatic_promotion'] is False and payload['adoption_authority'] is False
        assert value['automatic_promotion'] is False and all(block[name] is False for name in AUTHORITY)
        assert fixture.store.verify_decision(summary['run_id'], value['receipt_sha256']) == value
        code, saved = fixture.analyze_saved(summary['run_id'])
        assert saved['analysis']['algorithms'][-1]['result']['bayesian_decision'] == block


def test_the_receipt_of_a_plan_without_the_declaration_is_unchanged_in_shape(host):
    fixture = host.fixture()
    summary, value = host.receipt(fixture)
    assert 'bayesian_decision' not in value['analysis']
    assert 'bayesian_decision' not in value['receipt']['analysis_disposition']
    assert 'bayesian_decision' not in json.dumps(value['receipt'])
