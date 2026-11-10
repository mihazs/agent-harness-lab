"""Synthetic collection ordering and privacy controls; no real custody is claimed."""
import json

import pytest

from agent_harness_lab.evaluation import resource_collection, resource_decision, trial_usage
from tests.integration.evaluation.test_completed_trial_usage import synthetic_after_row
from tests.integration.evaluation.test_trial_usage import comparison_fixture, execute
from tests.unit.evaluation.test_resource_decision import TIME, token_scope_contract


def transport():
    calls = []
    def reply(*args):
        calls.append(1)
        output = [dict(type='message',id=f'synthetic-{len(calls)}',role='assistant',status='completed',
                       content=[dict(type='output_text',text='done')])]
        return json.dumps(dict(status='completed',output=output,usage=dict(input_tokens=5,output_tokens=2,total_tokens=7))).encode()
    return reply


def finish_trial(comparison,client,workspace,harness,deadline, *, missing=False):
    comparison.before(comparison.expected[len(comparison.rows)],workspace,harness,client)
    execute(comparison,client,workspace,harness,deadline)
    if missing: trial_usage._LIVE.pop(comparison,None)
    row = synthetic_after_row(comparison)
    comparison.after(row)
    return row


def test_collection_binds_schedule_and_preserves_missing_private_usage(tmp_path):
    comparison,client,workspace,harness,deadline = comparison_fixture(tmp_path,transport())
    contract = token_scope_contract(); rows = []
    frozen_hash = resource_decision.decide(contract, [], 'supported')['contract_sha256']
    with comparison:
        with resource_collection.UsageCollector(comparison,contract) as collector:
            contract['cache_policy'] = 'provider_cache_allowed'  # construction froze a copy
            rows.append(finish_trial(comparison,client,workspace,harness,deadline))
            with pytest.raises(ValueError,match='complete live'): collector.report(dict(trials=rows))
            for index in range(1,len(comparison.expected)):
                rows.append(finish_trial(comparison,client,workspace,harness,deadline,missing=index == 1))
            result = collector.report(dict(trials=rows))
            assert result['plan_sha256'] == comparison.spec.digest and result['contract_binding'] == 'live_before_first_trial'
            assert result['resource_contract_sha256'] == frozen_hash
            assert result['experiment_plan_contains_contract'] is False and len(result['samples']) == len(rows)
            assert result['samples'][1]['usage']['request_binding_coverage'] == 'unknown'
            for sample in result['samples']:
                assert all(category['total'] is None for category in sample['usage']['usage'].values())
                assert 'requests' not in sample['usage']
            assert result['source'] == 'provider_reported_unverified' and result['quality'] == 'unverified'
            assert result['publication_allowed'] is False and result['automatic_promotion'] is False
            with pytest.raises(ValueError,match='private'): collector.report(dict(trials=rows),public=True)
            changed = list(rows); changed[0] = dict(changed[0],status='failed')
            with pytest.raises(ValueError,match='trial bytes changed'): collector.report(dict(trials=changed))
            result['samples'].clear()
            assert len(collector.report(dict(trials=rows))['samples']) == len(rows)
        with pytest.raises(ValueError,match='complete live'): collector.report(dict(trials=rows))


def test_collector_cannot_attach_after_trial_starts_or_accept_legacy_contract(tmp_path):
    comparison,client,workspace,harness,deadline = comparison_fixture(tmp_path,transport())
    with comparison:
        legacy = token_scope_contract(); legacy.update(profile='resource-noninferiority-v3',schema_version=3)
        with pytest.raises(ValueError): resource_collection.UsageCollector(comparison,legacy)
        no_tokens = token_scope_contract(); no_tokens['endpoints'] = [dict(TIME)]
        with pytest.raises(ValueError,match='token contract'): resource_collection.UsageCollector(comparison,no_tokens)
        comparison.before(comparison.expected[0],workspace,harness,client)
        with pytest.raises(ValueError,match='before collecting'):
            with resource_collection.UsageCollector(comparison,token_scope_contract()): pass


def test_collection_failure_does_not_change_trial_custody_and_remains_unusable(tmp_path, monkeypatch):
    comparison,client,workspace,harness,deadline = comparison_fixture(tmp_path,transport())
    with comparison:
        with resource_collection.UsageCollector(comparison,token_scope_contract()) as collector:
            def failed_record(_): raise RuntimeError('synthetic collector failure')
            monkeypatch.setattr(collector,'_record',failed_record)
            row = finish_trial(comparison,client,workspace,harness,deadline)
            assert comparison.rows[-1]['trial_sha256'] == trial_usage.hashed(row)
            with pytest.raises(ValueError,match='complete live'): collector.report(dict(trials=[row]))


def test_collection_interruption_propagates_after_committed_trial(tmp_path, monkeypatch):
    comparison,client,workspace,harness,deadline = comparison_fixture(tmp_path,transport())
    with comparison:
        with resource_collection.UsageCollector(comparison,token_scope_contract()) as collector:
            def interrupted(_): raise KeyboardInterrupt
            monkeypatch.setattr(collector,'_record',interrupted)
            comparison.before(comparison.expected[0],workspace,harness,client)
            execute(comparison,client,workspace,harness,deadline)
            row = synthetic_after_row(comparison)
            with pytest.raises(KeyboardInterrupt): comparison.after(row)
            assert comparison.active is None and comparison.rows[-1]['trial_sha256'] == trial_usage.hashed(row)
            with pytest.raises(ValueError,match='complete live'): collector.report(dict(trials=[row]))
