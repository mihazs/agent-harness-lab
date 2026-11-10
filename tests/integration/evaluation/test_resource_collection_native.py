"""Synthetic transport with actual opt-in kernel workers and grader custody."""
import json
from unittest.mock import patch

from agent_harness_lab.evaluation import comparison_custody, resource_collection
from tests.integration.evaluation.test_repeated_workflow import kernel, kernel_only
from tests.integration.evaluation.test_repeated_workflow import kernel_case as kernel_case
from tests.integration.evaluation.test_repeated_workflow import kernel_scope as kernel_scope
from tests.unit.evaluation.test_resource_decision import token_scope_contract


@kernel_only
def test_collector_observes_real_custody_without_sending_usage_to_workers(kernel_case):
    enter,after,exit_ = (comparison_custody.Comparison.__enter__,comparison_custody.Comparison.after,
                         comparison_custody.Comparison.__exit__)
    active = {}; reports = []
    def start(comparison):
        value = enter(comparison)
        if not comparison.controls:
            collector = resource_collection.UsageCollector(comparison,token_scope_contract())
            collector.__enter__(); active[comparison] = (collector,[])
        return value
    def observe(comparison,row):
        after(comparison,row)
        if comparison in active:
            collector,rows = active[comparison]; rows.append(row)
            if len(rows) == len(comparison.expected): reports.append(collector.report(dict(trials=rows)))
    def stop(comparison,*errors):
        if comparison in active:
            collector,_ = active.pop(comparison); collector.__exit__(*errors)
        return exit_(comparison,*errors)
    runtime = kernel_case.root/'runtime'; pin = kernel.pinned_runtime(runtime)['sha256']
    with patch.object(comparison_custody.Comparison,'__enter__',start), patch.object(comparison_custody.Comparison,'after',observe), \
            patch.object(comparison_custody.Comparison,'__exit__',stop):
        code,result = kernel_case.invoke(runtime,pin,kernel_case.root/'output',source=True)
    assert code == 3 and result['analysis']['status'] == 'supported'
    assert len(reports) == 1 and len(reports[0]['samples']) == 12
    assert reports[0]['publication_allowed'] is False and reports[0]['automatic_promotion'] is False
    assert all(item['usage']['trial_completion'] == 'live_custody_accepted' for item in reports[0]['samples'])
    assert all(category['total'] is None for item in reports[0]['samples'] for category in item['usage']['usage'].values())
    assert all('ahl-live-plan-provider-usage-v1' not in json.dumps(call) for call in kernel_case.calls)
