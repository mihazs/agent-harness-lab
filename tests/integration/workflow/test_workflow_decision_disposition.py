"""Default `ahl improve` decision dispositions on synthetic cooperative runs only."""
from copy import deepcopy
import hashlib
import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.integration.workflow import test_workflow as workflow_fixture
from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.promotion import policy
from tests.support.unittest_cleanups import run_cleanups

# Same synthetic agent; one added mode keeps the candidate failing on both attempts.
FAILING = workflow_fixture.FAKE.replace("'worse' if mode=='refine' and c['attempt']==0",
                                        "'worse' if mode=='worse' or mode=='refine' and c['attempt']==0")
assert FAILING != workflow_fixture.FAKE
# Trial workspaces routinely hold symlinks (virtualenvs, node_modules/.bin).
SYMLINKING = FAILING.replace(" p('artifact').write_text('done' if good", " p('venv-link').symlink_to('phase'); p('artifact').write_text('done' if good")
assert SYMLINKING != FAILING
# Candidate fails attempt 0; the proposer then stops attempt 1 before any evaluation.
LATE_STOP = FAILING.replace("if mode=='no_change':", "if mode=='no_change' or mode=='late_stop' and c['attempt']==1:").replace(
    "'worse' if mode=='worse' or", "'worse' if mode in ('worse','late_stop') or")
assert LATE_STOP.count('late_stop') == 2
FORBIDDEN = {'recommended', 'supported', 'eligible', 'applied', 'certified', 'adopted', 'validated_improvement'}


class TestDefaultDecisionDisposition:
    @pytest.fixture(autouse=True)
    def _workflow(self):
        base = workflow_fixture.WorkflowTests(); base.setUp()  # Synthetic fixture only; its cases are not inherited.
        self.root, self.project, self.agent = base.root, base.project, base.agent
        self.agent.write_text(FAILING)
        yield
        run_cleanups(base)

    def run_cli(self, extra=()):
        output = self.root / 'run'
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = main(['improve', str(self.project), '--agent', 'codex', '--codex', str(self.agent),
                         '--claude', 'missing', '--timeout', '30', '--output', str(output), *extra])
        return code, json.loads((output / 'workflow.json').read_text())

    def assert_no_authority(self, result):
        decision = result['decision']
        assert decision['profile'] == policy.SINGLE_RUN_PROFILE
        assert decision['evidence_scope'] == 'single_run_descriptive_only'
        assert decision['next_evidence'] == 'requires_repeated_paired_experiment'
        for key in ('recommendation_authority', 'application_eligible', 'publication_allowed',
                    'automatic_promotion', 'adoption_authority'):
            assert decision[key] is False, key
        assert decision['promotion']['automatic'] is False
        assert decision['promotion']['isolation'] == 'unknown'
        assert decision['promotion']['status'] not in FORBIDDEN
        assert decision['disposition'] not in FORBIDDEN
        assert decision['independence'] == 'unverified'
        assert decision['observed']['repeats_per_task_arm'] == 1
        for reason in ('single_run_per_task_arm', 'no_frozen_experiment_plan', 'descriptive_smoke_cannot_certify',
                       'independent_roots_undeclared', 'isolation_qualification_unknown',
                       'certification_prerequisites_unverified'):
            assert reason in decision['reasons']
        assert set(decision['unmet_prerequisites']) == set(result['implementation_prerequisites']['mandatory_certification'])
        assert result['automatic_promotion'] is False
        assert result['changed_files'] == []
        assert (self.project / 'AGENTS.md').read_text() == self.original
        binding = decision['bindings']
        evaluation = Path(binding['evaluation'])
        assert 'evaluation_sha256' not in binding
        assert binding['results_sha256'] == hashlib.sha256((evaluation / 'results.json').read_bytes()).hexdigest()
        assert binding['describes_final_attempt'] == (binding['attempt'] == binding['final_attempt'])
        assert binding['manifest_sha256'] == hashlib.sha256((evaluation.parent / 'preparation' / f"evaluation-{binding['attempt']}"
                                         / 'manifest.json').read_bytes()).hexdigest()
        return decision

    def test_improved_candidate_explains_insufficient_single_run_evidence(self):
        self.original = 'bad'
        code, result = self.run_cli()
        assert (code, result['status']) == (3, 'inconclusive')
        assert result['recommendation'] == 'candidate_requires_independent_certification'
        decision = self.assert_no_authority(result)
        assert decision['disposition'] == 'insufficient_evidence'
        assert decision['observed_pattern'] == 'candidate_passed_where_baseline_failed'
        assert decision['promotion']['status'] == 'inconclusive'
        assert decision['observed']['pairs'] == dict(candidate_only_passed=2, baseline_only_passed=0,
                                                             both_passed=1, both_failed=0, unknown=0)
        assert decision['observed']['by_split']['regression']['both_passed'] == 1
        assert sorted(result['blocked_prerequisites']) == sorted(decision['unmet_prerequisites'])
        plan = json.loads(Path(result['plan']).read_text())
        assert plan['application_eligible'] is False
        assert decision['bindings']['candidate_sha256'] == plan['candidate_sha256']
        # Every surface the single-run candidate cannot adopt is deferred; the list comes from surfaces.matrix().
        assert plan['harness_change_plan']['deferred_surfaces'] == ['behavioral_application_pending_independent_certification', 'skill_lifecycle',
                          'hooks', 'workflows', 'agents', 'plugins', 'mcp', 'output_styles', 'settings']

    def test_no_change_reports_observed_absence_not_failure(self):
        self.original = 'good'
        (self.project / 'AGENTS.md').write_text('good')
        code, result = self.run_cli()
        assert (code, result['status'], result['reason']) == (0, 'no_change', 'evaluation_no_improvement')
        assert 'recommendation' not in result
        assert 'blocked_prerequisites' not in result
        decision = self.assert_no_authority(result)
        assert decision['disposition'] == 'insufficient_evidence'
        assert decision['observed_pattern'] == 'no_observed_difference'
        assert decision['observed']['pairs']['both_passed'] == 3

    def test_failed_candidate_keeps_status_and_binds_latest_attempt(self):
        self.original = 'bad'
        (self.project / 'mode').write_text('worse')
        code, result = self.run_cli()
        assert (code, result['status']) == (1, 'evaluation_failed')
        assert 'recommendation' not in result
        assert 'plan' not in result
        decision = self.assert_no_authority(result)
        assert decision['bindings']['attempt'] == 1
        assert decision['observed_pattern'] == 'candidate_failures_observed'
        assert decision['observed']['pairs'] == dict(candidate_only_passed=0, baseline_only_passed=0,
                                                             both_passed=1, both_failed=2, unknown=0)

    def test_trial_workspace_symlinks_keep_base_outcomes(self):
        self.agent.write_text(SYMLINKING)
        self.original = 'good'
        (self.project / 'AGENTS.md').write_text('good')
        code, result = self.run_cli()
        assert (code, result['status'], result.get('reason')) == (0, 'no_change', 'evaluation_no_improvement')
        assert result['decision']['observed_pattern'] == 'no_observed_difference'
        assert any(path.is_symlink() for path in (self.root / 'run' / 'evaluation-0').rglob('*'))

    def test_failed_candidate_with_trial_symlinks_keeps_base_outcome(self):
        self.agent.write_text(SYMLINKING.replace("mode=(project/'mode').read_text() if (project/'mode').exists() else 'ok'", "mode='worse'"))
        self.original = 'bad'
        code, result = self.run_cli()
        assert (code, result['status']) == (1, 'evaluation_failed')
        assert result['decision']['bindings']['attempt'] == 1

    def test_block_marks_when_a_later_attempt_stopped_before_evaluation(self):
        self.agent.write_text(LATE_STOP)
        self.original = 'bad'
        (self.project / 'mode').write_text('late_stop')
        code, result = self.run_cli()
        assert (code, result['status'], result['reason']) == (0, 'no_change', 'proposer_no_change')
        decision = self.assert_no_authority(result)
        assert (decision['bindings']['attempt'], decision['bindings']['final_attempt']) == (0, 1)
        assert decision['bindings']['describes_final_attempt'] is False
        assert decision['observed_pattern'] == 'candidate_failures_observed'

    def test_decision_failure_never_changes_the_run_result(self):
        self.original = 'good'
        (self.project / 'AGENTS.md').write_text('good')
        with patch('agent_harness_lab.promotion.policy.single_run_decision', side_effect=RuntimeError('synthetic')):
            code, result = self.run_cli()
        assert (code, result['status'], result['reason']) == (0, 'no_change', 'evaluation_no_improvement')
        assert (result['decision']['status'], result['decision']['reason']) == ('unavailable', 'RuntimeError')
        assert not public_export_allowed([result['decision']])

    def test_decision_block_is_never_publicly_exportable(self):
        self.original = 'bad'
        _, result = self.run_cli()
        decision = deepcopy(result['decision'])
        assert not public_export_allowed([decision])
        decision['publication_allowed'] = True  # The profile alone still blocks export.
        assert not public_export_allowed([decision])


class TestSingleRunDecision:
    manifest = dict(tasks=[dict(id='a', split='development', root='r1'), dict(id='b', split='regression', root='r2')])

    def trials(self, **statuses):
        return [dict(task=task, version=version, split=split, status=statuses.get(f'{version}_{task}', 'passed'))
                for task, split in (('a', 'development'), ('b', 'regression')) for version in ('baseline', 'candidate')]

    def decide(self, trials, inputs_unchanged=True):
        return policy.single_run_decision(self.manifest, trials, inputs_unchanged=inputs_unchanged,
                                          prerequisites=policy.implementation_prerequisites())

    def test_unknown_outcomes_never_become_pass_or_fail(self):
        decision = self.decide(self.trials(candidate_a='grader_error'))
        assert decision['observed']['pairs']['unknown'] == 1
        assert decision['observed']['pairs']['candidate_only_passed'] == 0
        assert decision['observed_pattern'] == 'unknown_trial_outcomes'
        assert 'unknown_trial_outcomes' in decision['reasons']

    def test_declared_roots_remain_unverified_and_changed_inputs_block(self):
        decision = self.decide(self.trials(baseline_a='failed'), inputs_unchanged=False)
        assert decision['observed']['declared_roots'] == 2
        assert 'independent_roots_unverified' in decision['reasons']
        assert (decision['disposition'], decision['promotion']['status']) == ('invalid_evidence', 'blocked')
        assert decision['observed_pattern'] == 'frozen_inputs_changed'

    def test_difference_outside_gate_splits_is_not_reported_as_no_difference(self):
        manifest = dict(tasks=[*self.manifest['tasks'], dict(id='c', split='heldout')])
        trials = self.trials() + [dict(task='c', version='baseline', split='heldout', status='failed'),
                                  dict(task='c', version='candidate', split='heldout', status='passed')]
        decision = policy.single_run_decision(manifest, trials, inputs_unchanged=True,
                                              prerequisites=policy.implementation_prerequisites())
        assert decision['observed']['by_split']['heldout']['candidate_only_passed'] == 1
        assert decision['observed_pattern'] == 'difference_outside_gate_splits'

    def test_trial_split_disagreeing_with_manifest_is_unknown(self):
        trials = self.trials(baseline_a='failed')
        trials[0]['split'] = 'regression'  # baseline 'a' is declared development
        decision = self.decide(trials)
        assert decision['observed']['split_mismatched_trials'] == 1
        assert decision['observed']['by_split']['development']['unknown'] == 1
        assert decision['observed_pattern'] == 'unknown_trial_outcomes'
        assert 'trial_split_mismatch' in decision['reasons']

    def test_incomplete_or_duplicate_coverage_is_rejected(self):
        trials = self.trials()
        with pytest.raises(ValueError): self.decide(trials[:-1])
        with pytest.raises(ValueError): self.decide(trials + [dict(trials[0])])
        duplicate = dict(tasks=[dict(id='a', split='development')] * 2)
        with pytest.raises(ValueError):
            policy.single_run_decision(duplicate, trials[:2], inputs_unchanged=True,
                                       prerequisites=policy.implementation_prerequisites())

