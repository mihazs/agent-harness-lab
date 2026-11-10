"""Synthetic quality policy frozen before a real kernel-backed comparison."""
import json
from unittest.mock import patch

import pytest

from agent_harness_lab.evaluation import repeated_workflow, resource_decision
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.oracle_history import HistoryStore
from agent_harness_lab.private_journal import encoded, record
from tests.integration.evaluation.test_repeated_workflow import kernel, kernel_only
from tests.integration.evaluation.test_repeated_workflow import kernel_case as kernel_case
from tests.integration.evaluation.test_repeated_workflow import kernel_scope as kernel_scope
from tests.integration.evaluation.test_resource_quality_policy import pin, policy
from tests.unit.evaluation.test_resource_decision import token_scope_contract


@kernel_only
def test_pretrial_policy_is_bound_and_tampering_blocks_reopen(kernel_case):
    manifest = record(kernel_case.manifest)[0]
    for task,prompt in zip(manifest['tasks'],['Write a blue result file.', 'Compute an ordered date range.', 'Check a numerical input boundary.']):
        task['prompt'] = prompt
    kernel_case.manifest.write_bytes(encoded(manifest))
    kernel_case.request_value.update(root_similarity=True,manifest_sha256=record(kernel_case.manifest)[1],bank_sha256=digest(kernel_case.bank))
    kernel_case.request.write_bytes(encoded(kernel_case.request_value))
    contract = token_scope_contract(); contract_pin = resource_decision.decide(contract, [], 'supported')['contract_sha256']
    original_spec,original_run = repeated_workflow.ExperimentSpec,HistoryStore.run
    def plan(*args,**kwargs): return original_spec(*args,**kwargs,resource_contract_sha256=contract_pin,resource_quality_policy_sha256=pin(policy()))
    def run(store,*args,**kwargs): return original_run(store,*args,**kwargs,resource_contract=contract,resource_coverage=True,resource_quality_policy=policy())
    runtime = kernel_case.root/'runtime'; image = kernel.pinned_runtime(runtime)['sha256']
    with patch.object(repeated_workflow,'ExperimentSpec',plan), patch.object(HistoryStore,'run',run):
        code,result = kernel_case.invoke(runtime,image,kernel_case.root/'output',source=True)
    assert code == 3
    store = HistoryStore(kernel_case.root/'output/private-history'); run_id = result['controller_run_id']
    checked = store.checked(run_id); assert checked[-1] == 'current'
    assert checked[3].root_similarity_sha256 and checked[3].resource_quality_policy_sha256 == pin(policy())
    assert store.analyze(run_id)['resource_decision'] == 'not_computed'
    path = store.case(run_id)/'resource-quality-policy.json'; original = path.read_bytes()
    altered = json.loads(original); altered['quality_criteria_sha256'] = 'b'*64; path.write_bytes(encoded(altered))
    with pytest.raises(ValueError,match='quality policy'): store.checked(run_id)
