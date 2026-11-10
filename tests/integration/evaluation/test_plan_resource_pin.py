"""Synthetic plan identity, collection pinning and private export controls."""
from dataclasses import replace
from contextlib import ExitStack
import hashlib
import json

import pytest

from agent_harness_lab.evaluation import benchmark, comparison_custody, resource_collection, resource_decision
from agent_harness_lab.evaluation.plan import ExperimentSpec, schedule
from tests.integration.evaluation.test_resource_collection import finish_trial, transport
from tests.integration.evaluation.test_trial_usage import comparison_fixture
from tests.integration.evaluation.test_oracle_history import OracleHistoryTests
from tests.unit.evaluation.test_benchmark_contracts import record
from tests.unit.evaluation.test_resource_decision import token_scope_contract


def contract_hash(contract):
    return resource_decision.decide(contract,[], 'supported')['contract_sha256']


def pinned_comparison(comparison, client, deadline, pin):
    spec = replace(comparison.spec,resource_contract_sha256=pin)
    return comparison_custody.Comparison(spec,comparison.fixtures,client,deadline,bundles=comparison.bundles)


def test_legacy_plan_bytes_and_trial_ids_are_unchanged(tmp_path):
    comparison,_,_,_,_ = comparison_fixture(tmp_path,transport())
    spec = comparison.spec; document = spec.to_dict()
    assert 'resource_contract_sha256' not in document
    assert spec.digest == hashlib.sha256(json.dumps(document,sort_keys=True).encode()).hexdigest()
    roundtrip = ExperimentSpec.parse(document)
    assert roundtrip.digest == spec.digest and schedule(roundtrip) == schedule(spec)
    assert schedule(replace(spec,resource_contract_sha256=None)) == schedule(spec)


@pytest.mark.parametrize('pin', ['a'*63, 'A'*64, 'g'*64, 7])
def test_malformed_resource_plan_pins_are_rejected(tmp_path, pin):
    comparison,_,_,_,_ = comparison_fixture(tmp_path,transport())
    with pytest.raises(ValueError,match='resource contract'):
        replace(comparison.spec,resource_contract_sha256=pin).validate()


def test_plan_pin_binds_matching_contract_and_changes_only_new_trial_ids(tmp_path):
    original,client,workspace,harness,deadline = comparison_fixture(tmp_path,transport())
    contract = token_scope_contract(); pin = contract_hash(contract)
    comparison = pinned_comparison(original,client,deadline,pin)
    assert comparison.spec.digest != original.spec.digest
    assert {row['trial_id'] for row in comparison.expected}.isdisjoint({row['trial_id'] for row in original.expected})
    assert ExperimentSpec.parse(comparison.spec.to_dict()).resource_contract_sha256 == pin
    rows = []
    with comparison:
        with resource_collection.UsageCollector(comparison,contract) as collector:
            for _ in comparison.expected: rows.append(finish_trial(comparison,client,workspace,harness,deadline))
            report = collector.report(dict(trials=rows))
            assert report['contract_binding'] == 'experiment_plan_digest'
            assert report['resource_contract_sha256'] == pin and report['experiment_plan_contains_contract_digest'] is True
            assert report['experiment_plan_contains_contract'] is False and report['publication_allowed'] is False
            assert report['automatic_promotion'] is False and report['quality'] == 'unverified'


def test_mismatched_plan_pin_is_refused_before_dispatch(tmp_path):
    original,client,_,_,deadline = comparison_fixture(tmp_path,transport())
    comparison = pinned_comparison(original,client,deadline,'a'*64)
    with comparison:
        with pytest.raises(ValueError,match='differs from experiment plan'):
            resource_collection.UsageCollector(comparison,token_scope_contract())
        assert client.requests == 0


def test_resource_contract_pin_cannot_be_exported_as_public_metadata():
    item = record(); item['resource_contract_sha256'] = 'a'*64
    assert not benchmark.public_export_allowed([item])


def test_synthetic_history_reopens_exact_resource_plan_pin():
    with ExitStack() as cleanups:
        fixture = OracleHistoryTests(cleanups.callback)
        original = ExperimentSpec.parse(fixture.manifest['experiment'])
        pin = contract_hash(token_scope_contract())
        fixture.manifest['experiment'] = replace(original,resource_contract_sha256=pin).to_dict()
        fixture.write_manifest()
        result,_ = fixture.run_history()
        assert result['state'] == 'sealed'
        reopened = fixture.store.historical_plan(result['run_id'])
        assert reopened['plan']['resource_contract_sha256'] == pin
        assert reopened['plan_sha256'] == replace(original,resource_contract_sha256=pin).digest
