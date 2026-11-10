"""Synthetic plan/contract verification; a match is not proof of execution or history."""
from contextlib import ExitStack
from dataclasses import replace

import pytest

from agent_harness_lab.evaluation import resource_decision
from agent_harness_lab.evaluation.plan import ExperimentSpec
from tests.integration.evaluation.test_oracle_history import OracleHistoryTests
from tests.unit.evaluation.test_resource_decision import timing_contract, token_scope_contract


def test_reopened_synthetic_history_checks_supplied_contract_without_authentication_claim():
    with ExitStack() as cleanups:
        fixture = OracleHistoryTests(cleanups.callback)
        original = ExperimentSpec.parse(fixture.manifest['experiment'])
        contract = token_scope_contract()
        pin = resource_decision.decide(contract,[], 'supported')['contract_sha256']
        fixture.manifest['experiment'] = replace(original,resource_contract_sha256=pin).to_dict()
        fixture.write_manifest()
        result,_ = fixture.run_history()
        assert result['state'] == 'sealed'
        reopened = fixture.store.historical_plan(result['run_id'])
        binding = resource_decision.verify_plan_resource_contract(reopened['plan'],contract)
        assert binding['status'] == 'matched' and binding['plan_sha256'] == reopened['plan_sha256']
        assert binding['history_authentication'] == 'not_checked' and binding['contract_bytes_persisted'] == 'not_checked'
        assert binding['plan_source'] == 'caller_supplied_unverified' and binding['resource_decision'] == 'not_computed'
        assert binding['publication_allowed'] is False and binding['automatic_promotion'] is False
        changed = token_scope_contract(); changed['endpoints'][0]['horizon'] += 1
        assert resource_decision.verify_plan_resource_contract(reopened['plan'],changed)['status'] == 'mismatch'
        changed_plan = dict(reopened['plan'],resource_contract_sha256='a'*64)
        assert resource_decision.verify_plan_resource_contract(changed_plan,contract)['status'] == 'mismatch'
        assert resource_decision.verify_plan_resource_contract(original,contract)['status'] == 'unbound'
        with pytest.raises(ValueError,match='private'):
            resource_decision.verify_plan_resource_contract(reopened['plan'],contract,public=True)
        with pytest.raises(ValueError,match='v4 token contract'):
            resource_decision.verify_plan_resource_contract(reopened['plan'],timing_contract())
