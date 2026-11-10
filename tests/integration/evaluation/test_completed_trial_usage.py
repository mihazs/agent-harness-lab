"""Synthetic controls for the private live completion sidecar."""
import pytest

from agent_harness_lab.evaluation import comparison_custody, trial_usage
from tests.integration.evaluation.test_trial_usage import comparison_fixture, execute, response


def synthetic_after_row(comparison):
    """Exercise after's ordering without claiming real worker or grader custody."""
    command = dict(status='completed')
    grade = dict(status='completed',exit_code=0)
    comparison.active['workers'] = [dict(command=command)]
    comparison.active['grade'] = dict(run_sha256=comparison_custody.hashed(grade),outcome=True)
    comparison._save()
    return dict(comparison.active['scheduled'],status='passed',execution=dict(worker_commands=[command]),
                grader=dict(execution=grade,outcome=True))


def test_completed_sidecar_only_after_accepted_trial_and_not_during_next_trial(tmp_path):
    comparison,client,workspace,harness,deadline = comparison_fixture(tmp_path,response)
    with comparison:
        comparison.before(comparison.expected[0],workspace,harness,client)
        execute(comparison,client,workspace,harness,deadline)
        with pytest.raises(ValueError,match='live completed'):
            trial_usage.latest_completed_trial_usage()
        row = synthetic_after_row(comparison)
        comparison.after(row)
        summary = trial_usage.latest_completed_trial_usage()
        assert summary['profile'] == 'ahl-completed-trial-provider-usage-v1'
        assert summary['trial_completion'] == 'live_custody_accepted' and summary['request_binding_coverage'] == 'matched'
        assert summary['usage']['input_tokens']['known_subtotal'] == 5
        assert all(value['total'] is None for value in summary['usage'].values())
        assert summary['source'] == 'provider_reported_unverified' and summary['billing_completeness'] == 'unknown'
        assert summary['durable'] is False and summary['publication_allowed'] is False
        assert summary['automatic_promotion'] is False and summary['certification_authority'] is False
        summary['usage']['input_tokens']['known_subtotal'] = 0
        assert trial_usage.latest_completed_trial_usage()['usage']['input_tokens']['known_subtotal'] == 5
        with pytest.raises(ValueError,match='private'): trial_usage.latest_completed_trial_usage(public=True)
        comparison.before(comparison.expected[1],workspace,harness,client)
        with pytest.raises(ValueError,match='live completed'): trial_usage.latest_completed_trial_usage()
    with pytest.raises(ValueError,match='live completed'): trial_usage.latest_completed_trial_usage()


def test_failed_trial_acceptance_never_publishes_a_completed_sidecar(tmp_path):
    comparison,client,workspace,harness,deadline = comparison_fixture(tmp_path,response)
    with comparison:
        comparison.before(comparison.expected[0],workspace,harness,client)
        execute(comparison,client,workspace,harness,deadline)
        row = synthetic_after_row(comparison)
        row['trial_id'] = 'forged'
        with pytest.raises(ValueError,match='identity changed'): comparison.after(row)
        with pytest.raises(ValueError,match='live completed'): trial_usage.latest_completed_trial_usage()


def test_missing_live_usage_does_not_become_complete_after_trial_acceptance(tmp_path):
    comparison,client,workspace,harness,deadline = comparison_fixture(tmp_path,response)
    with comparison:
        comparison.before(comparison.expected[0],workspace,harness,client)
        execute(comparison,client,workspace,harness,deadline)
        trial_usage._LIVE.pop(comparison,None)
        comparison.after(synthetic_after_row(comparison))
        summary = trial_usage.latest_completed_trial_usage()
        assert summary['request_binding_coverage'] == 'unknown' and summary['unreported_attempts'] == 1
        assert all(value['total'] is None and value['known_subtotal'] is None for value in summary['usage'].values())
