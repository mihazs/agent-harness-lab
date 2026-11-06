"""Default `ahl improve` decision dispositions on synthetic cooperative runs only."""
from copy import deepcopy
import hashlib
import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import unittest
from unittest.mock import patch

if __package__:
    from . import test_workflow as workflow_fixture
else:
    import test_workflow as workflow_fixture
from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.promotion import policy

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


class DefaultDecisionDisposition(unittest.TestCase):
    def setUp(self):
        workflow_fixture.WorkflowTests.setUp(self)  # Synthetic fixture only; its cases are not inherited.
        self.agent.write_text(FAILING)

    def run_cli(self, extra=()):
        output = self.root / 'run'
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = main(['improve', str(self.project), '--agent', 'codex', '--codex', str(self.agent),
                         '--claude', 'missing', '--timeout', '30', '--output', str(output), *extra])
        return code, json.loads((output / 'workflow.json').read_text())

    def assert_no_authority(self, result):
        decision = result['decision']
        self.assertEqual(decision['profile'], policy.SINGLE_RUN_PROFILE)
        self.assertEqual(decision['evidence_scope'], 'single_run_descriptive_only')
        self.assertEqual(decision['next_evidence'], 'requires_repeated_paired_experiment')
        for key in ('recommendation_authority', 'application_eligible', 'publication_allowed',
                    'automatic_promotion', 'adoption_authority'):
            self.assertIs(decision[key], False, key)
        self.assertIs(decision['promotion']['automatic'], False)
        self.assertEqual(decision['promotion']['isolation'], 'unknown')
        self.assertNotIn(decision['promotion']['status'], FORBIDDEN)
        self.assertNotIn(decision['disposition'], FORBIDDEN)
        self.assertEqual(decision['independence'], 'unverified')
        self.assertEqual(decision['observed']['repeats_per_task_arm'], 1)
        for reason in ('single_run_per_task_arm', 'no_frozen_experiment_plan', 'descriptive_smoke_cannot_certify',
                       'independent_roots_undeclared', 'isolation_qualification_unknown',
                       'certification_prerequisites_unverified'):
            self.assertIn(reason, decision['reasons'])
        self.assertEqual(set(decision['unmet_prerequisites']),
                         set(result['implementation_prerequisites']['mandatory_certification']))
        self.assertIs(result['automatic_promotion'], False)
        self.assertEqual(result['changed_files'], [])
        self.assertEqual((self.project / 'AGENTS.md').read_text(), self.original)
        binding = decision['bindings']
        evaluation = Path(binding['evaluation'])
        self.assertNotIn('evaluation_sha256', binding)
        self.assertEqual(binding['results_sha256'], hashlib.sha256((evaluation / 'results.json').read_bytes()).hexdigest())
        self.assertEqual(binding['describes_final_attempt'], binding['attempt'] == binding['final_attempt'])
        self.assertEqual(binding['manifest_sha256'],
                         hashlib.sha256((evaluation.parent / 'preparation' / f"evaluation-{binding['attempt']}"
                                         / 'manifest.json').read_bytes()).hexdigest())
        return decision

    def test_improved_candidate_explains_insufficient_single_run_evidence(self):
        self.original = 'bad'
        code, result = self.run_cli()
        self.assertEqual((code, result['status']), (3, 'inconclusive'))
        self.assertEqual(result['recommendation'], 'candidate_requires_independent_certification')
        decision = self.assert_no_authority(result)
        self.assertEqual(decision['disposition'], 'insufficient_evidence')
        self.assertEqual(decision['observed_pattern'], 'candidate_passed_where_baseline_failed')
        self.assertEqual(decision['promotion']['status'], 'inconclusive')
        self.assertEqual(decision['observed']['pairs'], dict(candidate_only_passed=2, baseline_only_passed=0,
                                                             both_passed=1, both_failed=0, unknown=0))
        self.assertEqual(decision['observed']['by_split']['regression']['both_passed'], 1)
        self.assertEqual(sorted(result['blocked_prerequisites']), sorted(decision['unmet_prerequisites']))
        plan = json.loads(Path(result['plan']).read_text())
        self.assertIs(plan['application_eligible'], False)
        self.assertEqual(decision['bindings']['candidate_sha256'], plan['candidate_sha256'])
        # Every surface the single-run candidate cannot adopt is deferred; the list comes from surfaces.matrix().
        self.assertEqual(plan['harness_change_plan']['deferred_surfaces'],
                         ['behavioral_application_pending_independent_certification', 'skill_lifecycle',
                          'hooks', 'workflows', 'agents', 'plugins', 'mcp', 'output_styles', 'settings'])

    def test_no_change_reports_observed_absence_not_failure(self):
        self.original = 'good'
        (self.project / 'AGENTS.md').write_text('good')
        code, result = self.run_cli()
        self.assertEqual((code, result['status'], result['reason']), (0, 'no_change', 'evaluation_no_improvement'))
        self.assertNotIn('recommendation', result)
        self.assertNotIn('blocked_prerequisites', result)
        decision = self.assert_no_authority(result)
        self.assertEqual(decision['disposition'], 'insufficient_evidence')
        self.assertEqual(decision['observed_pattern'], 'no_observed_difference')
        self.assertEqual(decision['observed']['pairs']['both_passed'], 3)

    def test_failed_candidate_keeps_status_and_binds_latest_attempt(self):
        self.original = 'bad'
        (self.project / 'mode').write_text('worse')
        code, result = self.run_cli()
        self.assertEqual((code, result['status']), (1, 'evaluation_failed'))
        self.assertNotIn('recommendation', result)
        self.assertNotIn('plan', result)
        decision = self.assert_no_authority(result)
        self.assertEqual(decision['bindings']['attempt'], 1)
        self.assertEqual(decision['observed_pattern'], 'candidate_failures_observed')
        self.assertEqual(decision['observed']['pairs'], dict(candidate_only_passed=0, baseline_only_passed=0,
                                                             both_passed=1, both_failed=2, unknown=0))

    def test_trial_workspace_symlinks_keep_base_outcomes(self):
        self.agent.write_text(SYMLINKING)
        self.original = 'good'
        (self.project / 'AGENTS.md').write_text('good')
        code, result = self.run_cli()
        self.assertEqual((code, result['status'], result.get('reason')), (0, 'no_change', 'evaluation_no_improvement'))
        self.assertEqual(result['decision']['observed_pattern'], 'no_observed_difference')
        self.assertTrue(any(path.is_symlink() for path in (self.root / 'run' / 'evaluation-0').rglob('*')))

    def test_failed_candidate_with_trial_symlinks_keeps_base_outcome(self):
        self.agent.write_text(SYMLINKING.replace("mode=(project/'mode').read_text() if (project/'mode').exists() else 'ok'", "mode='worse'"))
        self.original = 'bad'
        code, result = self.run_cli()
        self.assertEqual((code, result['status']), (1, 'evaluation_failed'))
        self.assertEqual(result['decision']['bindings']['attempt'], 1)

    def test_block_marks_when_a_later_attempt_stopped_before_evaluation(self):
        self.agent.write_text(LATE_STOP)
        self.original = 'bad'
        (self.project / 'mode').write_text('late_stop')
        code, result = self.run_cli()
        self.assertEqual((code, result['status'], result['reason']), (0, 'no_change', 'proposer_no_change'))
        decision = self.assert_no_authority(result)
        self.assertEqual((decision['bindings']['attempt'], decision['bindings']['final_attempt']), (0, 1))
        self.assertIs(decision['bindings']['describes_final_attempt'], False)
        self.assertEqual(decision['observed_pattern'], 'candidate_failures_observed')

    def test_decision_failure_never_changes_the_run_result(self):
        self.original = 'good'
        (self.project / 'AGENTS.md').write_text('good')
        with patch('agent_harness_lab.promotion.policy.single_run_decision', side_effect=RuntimeError('synthetic')):
            code, result = self.run_cli()
        self.assertEqual((code, result['status'], result['reason']), (0, 'no_change', 'evaluation_no_improvement'))
        self.assertEqual((result['decision']['status'], result['decision']['reason']), ('unavailable', 'RuntimeError'))
        self.assertFalse(public_export_allowed([result['decision']]))

    def test_decision_block_is_never_publicly_exportable(self):
        self.original = 'bad'
        _, result = self.run_cli()
        decision = deepcopy(result['decision'])
        self.assertFalse(public_export_allowed([decision]))
        decision['publication_allowed'] = True  # The profile alone still blocks export.
        self.assertFalse(public_export_allowed([decision]))


class SingleRunDecisionUnit(unittest.TestCase):
    manifest = dict(tasks=[dict(id='a', split='development', root='r1'), dict(id='b', split='regression', root='r2')])

    def trials(self, **statuses):
        return [dict(task=task, version=version, split=split, status=statuses.get(f'{version}_{task}', 'passed'))
                for task, split in (('a', 'development'), ('b', 'regression')) for version in ('baseline', 'candidate')]

    def decide(self, trials, inputs_unchanged=True):
        return policy.single_run_decision(self.manifest, trials, inputs_unchanged=inputs_unchanged,
                                          prerequisites=policy.implementation_prerequisites())

    def test_unknown_outcomes_never_become_pass_or_fail(self):
        decision = self.decide(self.trials(candidate_a='grader_error'))
        self.assertEqual(decision['observed']['pairs']['unknown'], 1)
        self.assertEqual(decision['observed']['pairs']['candidate_only_passed'], 0)
        self.assertEqual(decision['observed_pattern'], 'unknown_trial_outcomes')
        self.assertIn('unknown_trial_outcomes', decision['reasons'])

    def test_declared_roots_remain_unverified_and_changed_inputs_block(self):
        decision = self.decide(self.trials(baseline_a='failed'), inputs_unchanged=False)
        self.assertEqual(decision['observed']['declared_roots'], 2)
        self.assertIn('independent_roots_unverified', decision['reasons'])
        self.assertEqual((decision['disposition'], decision['promotion']['status']), ('invalid_evidence', 'blocked'))
        self.assertEqual(decision['observed_pattern'], 'frozen_inputs_changed')

    def test_difference_outside_gate_splits_is_not_reported_as_no_difference(self):
        manifest = dict(tasks=[*self.manifest['tasks'], dict(id='c', split='heldout')])
        trials = self.trials() + [dict(task='c', version='baseline', split='heldout', status='failed'),
                                  dict(task='c', version='candidate', split='heldout', status='passed')]
        decision = policy.single_run_decision(manifest, trials, inputs_unchanged=True,
                                              prerequisites=policy.implementation_prerequisites())
        self.assertEqual(decision['observed']['by_split']['heldout']['candidate_only_passed'], 1)
        self.assertEqual(decision['observed_pattern'], 'difference_outside_gate_splits')

    def test_trial_split_disagreeing_with_manifest_is_unknown(self):
        trials = self.trials(baseline_a='failed')
        trials[0]['split'] = 'regression'  # baseline 'a' is declared development
        decision = self.decide(trials)
        self.assertEqual(decision['observed']['split_mismatched_trials'], 1)
        self.assertEqual(decision['observed']['by_split']['development']['unknown'], 1)
        self.assertEqual(decision['observed_pattern'], 'unknown_trial_outcomes')
        self.assertIn('trial_split_mismatch', decision['reasons'])

    def test_incomplete_or_duplicate_coverage_is_rejected(self):
        trials = self.trials()
        with self.assertRaises(ValueError): self.decide(trials[:-1])
        with self.assertRaises(ValueError): self.decide(trials + [dict(trials[0])])
        duplicate = dict(tasks=[dict(id='a', split='development')] * 2)
        with self.assertRaises(ValueError):
            policy.single_run_decision(duplicate, trials[:2], inputs_unchanged=True,
                                       prerequisites=policy.implementation_prerequisites())


if __name__ == '__main__':
    unittest.main()
