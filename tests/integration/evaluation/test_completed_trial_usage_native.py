"""Unmocked worker and grader custody with a synthetic local inference transport."""
from unittest.mock import patch

from agent_harness_lab.evaluation import comparison_custody, trial_usage
from tests.integration.evaluation.test_repeated_workflow import kernel, kernel_only
from tests.integration.evaluation.test_repeated_workflow import kernel_case as kernel_case
from tests.integration.evaluation.test_repeated_workflow import kernel_scope as kernel_scope


@kernel_only
def test_completed_usage_follows_actual_comparison_acceptance(kernel_case):
    captured = []
    after = comparison_custody.Comparison.after
    def observe(comparison, row):
        after(comparison,row)
        summary = trial_usage.latest_completed_trial_usage()
        assert summary['trial_completion'] == 'live_custody_accepted'
        assert summary['request_binding_coverage'] == 'matched'
        assert summary['source'] == 'provider_reported_unverified'
        assert all(value['total'] is None for value in summary['usage'].values())
        assert summary['publication_allowed'] is False and summary['automatic_promotion'] is False
        captured.append(summary)
    runtime = kernel_case.root/'runtime'; pin = kernel.pinned_runtime(runtime)['sha256']
    with patch.object(comparison_custody.Comparison,'after',observe):
        code,result = kernel_case.invoke(runtime,pin,kernel_case.root/'output',source=True)
    assert code == 3 and result['analysis']['status'] == 'supported'
    assert len(captured) == 12 and len({item['scheduled_trial_sha256'] for item in captured}) == 12
