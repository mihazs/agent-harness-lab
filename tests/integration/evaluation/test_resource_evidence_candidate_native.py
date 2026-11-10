"""Unsealed candidate with synthetic transport and actual opt-in worker custody."""
import json
from unittest.mock import patch

from agent_harness_lab.evaluation import comparison_custody, repeated_workflow, resource_collection, resource_decision
from tests.integration.evaluation.test_repeated_workflow import kernel, kernel_only
from tests.integration.evaluation.test_repeated_workflow import kernel_case as kernel_case
from tests.integration.evaluation.test_repeated_workflow import kernel_scope as kernel_scope
from tests.unit.evaluation.test_resource_decision import token_scope_contract


@kernel_only
def test_resource_candidate_uses_real_live_custody_without_provider_leak(kernel_case):
    contract = token_scope_contract(); pin = resource_decision.decide(contract,[], 'supported')['contract_sha256']
    original_spec = repeated_workflow.ExperimentSpec
    enter,after,exit_ = (comparison_custody.Comparison.__enter__,comparison_custody.Comparison.after,
                         comparison_custody.Comparison.__exit__)
    active = {}; candidates = []
    def plan(*args,**kwargs): return original_spec(*args,**kwargs,resource_contract_sha256=pin)
    def start(comparison):
        value = enter(comparison)
        if not comparison.controls:
            collector = resource_collection.UsageCollector(comparison,contract)
            collector.__enter__(); active[comparison] = (collector,[])
        return value
    def observe(comparison,row):
        after(comparison,row)
        if comparison in active:
            collector,rows = active[comparison]; rows.append(row)
            if len(rows) == len(comparison.expected): candidates.append(collector.evidence_candidate(dict(trials=rows)))
    def stop(comparison,*errors):
        if comparison in active:
            collector,_ = active.pop(comparison); collector.__exit__(*errors)
        return exit_(comparison,*errors)
    runtime = kernel_case.root/'runtime'; pin_image = kernel.pinned_runtime(runtime)['sha256']
    with patch.object(repeated_workflow,'ExperimentSpec',plan), patch.object(comparison_custody.Comparison,'__enter__',start), \
            patch.object(comparison_custody.Comparison,'after',observe), patch.object(comparison_custody.Comparison,'__exit__',stop):
        code,result = kernel_case.invoke(runtime,pin_image,kernel_case.root/'output',source=True)
    assert code == 3 and result['analysis']['status'] == 'supported'
    assert len(candidates) == 1 and len(candidates[0]['samples']) == 12
    assert candidates[0]['state'] == 'live_candidate_unsealed' and candidates[0]['durability'] == 'not_persisted'
    assert candidates[0]['resource_contract_sha256'] == pin and candidates[0]['publication_allowed'] is False
    assert all('ahl-resource-evidence-candidate-v1' not in json.dumps(call) for call in kernel_case.calls)
