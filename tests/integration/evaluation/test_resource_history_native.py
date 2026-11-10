"""Versioned resource history with synthetic transport and actual kernel custody."""
import json
from unittest.mock import patch

import pytest

from agent_harness_lab.evaluation import oracle_history, repeated_workflow, resource_collection, resource_decision
from agent_harness_lab.evaluation.oracle_history import HistoryStore
from tests.integration.evaluation.test_repeated_workflow import kernel, kernel_only
from tests.integration.evaluation.test_repeated_workflow import kernel_case as kernel_case
from tests.integration.evaluation.test_repeated_workflow import kernel_scope as kernel_scope
from tests.unit.evaluation.test_resource_decision import token_scope_contract


@kernel_only
def test_sealed_resource_history_reopens_and_rejects_artifact_tampering(kernel_case):
    contract = token_scope_contract(); pin = resource_decision.decide(contract,[], 'supported')['contract_sha256']
    original_spec,original_run = repeated_workflow.ExperimentSpec,HistoryStore.run
    def plan(*args,**kwargs): return original_spec(*args,**kwargs,resource_contract_sha256=pin)
    def run(store,*args,**kwargs): return original_run(store,*args,**kwargs,resource_contract=contract)
    runtime = kernel_case.root/'runtime'; image = kernel.pinned_runtime(runtime)['sha256']
    with patch.object(repeated_workflow,'ExperimentSpec',plan), patch.object(HistoryStore,'run',run):
        code,result = kernel_case.invoke(runtime,image,kernel_case.root/'output',source=True)
    assert code == 3 and result['analysis']['status'] == 'supported'
    assert result['analysis']['resource_decision'] == 'not_computed' and result['analysis']['resource_gate'] == 'not_evaluated'
    store = HistoryStore(kernel_case.root/'output/private-history'); run_id = result['controller_run_id']
    checked = store.checked(run_id)
    assert checked[2]['schema_version'] == 7 and checked[-1] == 'current'
    readback = store.resource_evidence(run_id)
    artifact = readback['artifact']; candidate = artifact['candidate']
    assert readback['history'] == 'sealed_readback' and readback['provider'] == 'unverified'
    assert readback['history_authentication'] == 'not_established' and readback['certification_authority'] is False
    assert candidate['resource_contract_sha256'] == pin and len(candidate['samples']) == 12
    assert all(category['total'] is None for sample in candidate['samples'] for category in sample['usage']['usage'].values())
    assert not readback['publication_allowed'] and not readback['automatic_promotion']
    assert all('ahl-sealed-resource-evidence-v1' not in json.dumps(call) for call in kernel_case.calls)
    with pytest.raises(ValueError,match='private'): store.resource_evidence(run_id,public=True)
    path = store.case(run_id)/'resource-evidence.json'; original = path.read_bytes()
    try:
        path.write_bytes(original+b' ')
        with pytest.raises(ValueError,match='resource artifact changed'): store.resource_evidence(run_id)
    finally:
        path.write_bytes(original)
    assert store.resource_evidence(run_id)['artifact'] == artifact
    calls = []; original_record = oracle_history.record
    def race(target,*args,**kwargs):
        if target == path:
            calls.append(1)
            if len(calls) == 2: path.write_bytes(original+b' ')
        return original_record(target,*args,**kwargs)
    try:
        with patch.object(oracle_history,'record',race), pytest.raises(ValueError,match='resource readback changed'):
            store.resource_evidence(run_id)
    finally:
        path.write_bytes(original)


@kernel_only
def test_interrupted_resource_history_stays_incomplete_and_has_no_artifact(kernel_case):
    contract = token_scope_contract(); pin = resource_decision.decide(contract,[], 'supported')['contract_sha256']
    original_spec,original_run = repeated_workflow.ExperimentSpec,HistoryStore.run
    def plan(*args,**kwargs): return original_spec(*args,**kwargs,resource_contract_sha256=pin)
    def run(store,*args,**kwargs): return original_run(store,*args,**kwargs,resource_contract=contract)
    kernel_case.mode = 'pending'
    runtime = kernel_case.root/'runtime'; image = kernel.pinned_runtime(runtime)['sha256']
    with patch.object(repeated_workflow,'ExperimentSpec',plan), patch.object(HistoryStore,'run',run):
        _,result = kernel_case.invoke(runtime,image,kernel_case.root/'output')
    assert result['status'] == 'incomplete' and 'decision' not in result
    store = HistoryStore(kernel_case.root/'output/private-history'); run_id = result['controller_run_id']
    checked = store.checked(run_id)
    assert checked[2]['schema_version'] == 7 and checked[-1] == 'incomplete'
    assert not (store.case(run_id)/'resource-evidence.json').exists()
    with pytest.raises(ValueError,match='current sealed'): store.resource_evidence(run_id)


@kernel_only
def test_keyboard_interrupt_during_collection_does_not_seal_evidence(kernel_case):
    contract = token_scope_contract(); pin = resource_decision.decide(contract,[], 'supported')['contract_sha256']
    original_spec,original_run = repeated_workflow.ExperimentSpec,HistoryStore.run
    def plan(*args,**kwargs): return original_spec(*args,**kwargs,resource_contract_sha256=pin)
    def run(store,*args,**kwargs): return original_run(store,*args,**kwargs,resource_contract=contract)
    def interrupt(*args): raise KeyboardInterrupt
    runtime = kernel_case.root/'runtime'; image = kernel.pinned_runtime(runtime)['sha256']
    with patch.object(repeated_workflow,'ExperimentSpec',plan), patch.object(HistoryStore,'run',run), \
            patch.object(resource_collection.UsageCollector,'_record',interrupt):
        _,result = kernel_case.invoke(runtime,image,kernel_case.root/'output')
    assert result['status'] == 'interrupted'
    store = HistoryStore(kernel_case.root/'output/private-history')
    cases = [path for path in (store.root/'cases').iterdir() if (path/'inputs.json').exists()]
    assert len(cases) == 1
    checked = store.checked(cases[0].name)
    assert checked[2]['schema_version'] == 7 and checked[-1] == 'incomplete'
    assert not (cases[0]/'resource-evidence.json').exists()
    with pytest.raises(ValueError,match='current sealed'): store.resource_evidence(cases[0].name)


@kernel_only
def test_interruption_after_artifact_write_cannot_publish_it_as_sealed(kernel_case):
    contract = token_scope_contract(); pin = resource_decision.decide(contract,[], 'supported')['contract_sha256']
    original_spec,original_run,original_write = repeated_workflow.ExperimentSpec,HistoryStore.run,oracle_history.write_bytes
    def plan(*args,**kwargs): return original_spec(*args,**kwargs,resource_contract_sha256=pin)
    def run(store,*args,**kwargs): return original_run(store,*args,**kwargs,resource_contract=contract)
    def write(path,raw):
        if path.name == 'evidence.json': raise KeyboardInterrupt
        return original_write(path,raw)
    runtime = kernel_case.root/'runtime'; image = kernel.pinned_runtime(runtime)['sha256']
    with patch.object(repeated_workflow,'ExperimentSpec',plan), patch.object(HistoryStore,'run',run), \
            patch.object(oracle_history,'write_bytes',write):
        _,result = kernel_case.invoke(runtime,image,kernel_case.root/'output')
    assert result['status'] == 'interrupted'
    store = HistoryStore(kernel_case.root/'output/private-history')
    cases = [path for path in (store.root/'cases').iterdir() if (path/'inputs.json').exists()]
    assert len(cases) == 1 and (cases[0]/'resource-evidence.json').exists()
    assert store.checked(cases[0].name)[-1] == 'incomplete'
    with pytest.raises(ValueError,match='current sealed'): store.resource_evidence(cases[0].name)


@kernel_only
def test_resource_artifact_byte_change_before_seal_stays_incomplete(kernel_case):
    contract = token_scope_contract(); pin = resource_decision.decide(contract,[], 'supported')['contract_sha256']
    original_spec,original_run,original_append = repeated_workflow.ExperimentSpec,HistoryStore.run,HistoryStore.append
    def plan(*args,**kwargs): return original_spec(*args,**kwargs,resource_contract_sha256=pin)
    def run(store,*args,**kwargs): return original_run(store,*args,**kwargs,resource_contract=contract)
    def append(store,case,events,kind,state,payload,**kwargs):
        if kind == 'run_finished' and state == 'sealed':
            path = case/'resource-evidence.json'; path.write_bytes(path.read_bytes()+b' ')
        return original_append(store,case,events,kind,state,payload,**kwargs)
    runtime = kernel_case.root/'runtime'; image = kernel.pinned_runtime(runtime)['sha256']
    with patch.object(repeated_workflow,'ExperimentSpec',plan), patch.object(HistoryStore,'run',run), \
            patch.object(HistoryStore,'append',append):
        _,result = kernel_case.invoke(runtime,image,kernel_case.root/'output')
    assert result['status'] == 'incomplete' and 'decision' not in result
    store = HistoryStore(kernel_case.root/'output/private-history'); run_id = result['controller_run_id']
    assert store.checked(run_id)[-1] == 'incomplete'
    with pytest.raises(ValueError,match='current sealed'): store.resource_evidence(run_id)


@kernel_only
def test_versioned_coverage_seals_and_reopens_without_authority(kernel_case):
    contract = token_scope_contract(); pin = resource_decision.decide(contract, [], 'supported')['contract_sha256']
    original_spec,original_run = repeated_workflow.ExperimentSpec,HistoryStore.run
    def plan(*args,**kwargs): return original_spec(*args,**kwargs,resource_contract_sha256=pin)
    def run(store,*args,**kwargs): return original_run(store,*args,**kwargs,resource_contract=contract,resource_coverage=True)
    runtime = kernel_case.root/'runtime'; image = kernel.pinned_runtime(runtime)['sha256']
    with patch.object(repeated_workflow,'ExperimentSpec',plan), patch.object(HistoryStore,'run',run):
        code,result = kernel_case.invoke(runtime,image,kernel_case.root/'output',source=True)
    assert code == 3 and result['analysis']['status'] == 'supported'
    store = HistoryStore(kernel_case.root/'output/private-history'); run_id = result['controller_run_id']
    assert store.checked(run_id)[2]['schema_version'] == 8
    readback = store.resource_evidence(run_id); artifact = readback['artifact']
    assert artifact['schema_version'] == 2 and readback['provider'] == 'unverified'
    assert not readback['automatic_promotion'] and not readback['publication_allowed']
    for sample in artifact['candidate']['samples']:
        usage = sample['usage']; attempts = usage['request_attempts']
        for category in usage['usage'].values():
            if usage['request_binding_coverage'] == 'matched' and attempts and category['known_requests'] == attempts:
                assert category['total'] == category['known_subtotal']
            else:
                assert category['total'] is None
    assert store.analyze(run_id)['resource_decision'] == 'not_computed'
