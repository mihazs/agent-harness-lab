"""Synthetic private resource evidence preparation; no history seal is asserted."""
from dataclasses import replace

import pytest

from agent_harness_lab.evaluation import comparison_custody, resource_collection, resource_decision
from tests.integration.evaluation.test_resource_collection import finish_trial, transport
from tests.integration.evaluation.test_trial_usage import comparison_fixture
from tests.unit.evaluation.test_resource_decision import token_scope_contract


def test_candidate_contains_frozen_declarations_and_unknown_samples_only_while_live(tmp_path):
    original,client,workspace,harness,deadline = comparison_fixture(tmp_path,transport())
    contract = token_scope_contract(); pin = resource_decision.decide(contract,[], 'supported')['contract_sha256']
    spec = replace(original.spec,resource_contract_sha256=pin)
    comparison = comparison_custody.Comparison(spec,original.fixtures,client,deadline,bundles=original.bundles)
    rows = []
    with comparison:
        with resource_collection.UsageCollector(comparison,contract) as collector:
            for index in range(len(comparison.expected)):
                rows.append(finish_trial(comparison,client,workspace,harness,deadline,missing=index == 1))
            result = dict(trials=rows)
            candidate = collector.evidence_candidate(result)
            assert candidate['profile'] == 'ahl-resource-evidence-candidate-v1'
            assert candidate['resource_contract_sha256'] == pin and candidate['resource_contract'] == contract
            assert candidate['result_canonical_sha256'] == comparison_custody.hashed(result)
            assert candidate['custody_canonical_sha256'] == comparison_custody.hashed(comparison.finish(result))
            assert candidate['samples'][1]['usage']['request_binding_coverage'] == 'unknown'
            assert all(category['total'] is None for item in candidate['samples'] for category in item['usage']['usage'].values())
            assert candidate['state'] == 'live_candidate_unsealed' and candidate['durability'] == 'not_persisted'
            assert candidate['source'] == 'provider_reported_unverified' and candidate['publication_allowed'] is False
            assert candidate['automatic_promotion'] is False and candidate['certification_authority'] is False
            candidate['resource_contract']['cache_policy'] = 'provider_cache_allowed'
            assert collector.evidence_candidate(result)['resource_contract'] == contract
            with pytest.raises(ValueError,match='private'): collector.evidence_candidate(result,public=True)
            changed = list(rows); changed[0] = dict(changed[0],status='failed')
            with pytest.raises(ValueError,match='trial bytes changed'): collector.evidence_candidate(dict(trials=changed))
        with pytest.raises(ValueError,match='complete live'): collector.evidence_candidate(result)


def test_unpinned_plan_cannot_prepare_resource_evidence_candidate(tmp_path):
    comparison,client,workspace,harness,deadline = comparison_fixture(tmp_path,transport())
    rows = []
    with comparison:
        with resource_collection.UsageCollector(comparison,token_scope_contract()) as collector:
            for _ in comparison.expected: rows.append(finish_trial(comparison,client,workspace,harness,deadline))
            with pytest.raises(ValueError,match='pinned in the plan'):
                collector.evidence_candidate(dict(trials=rows))
