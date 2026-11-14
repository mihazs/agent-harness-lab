"""A vacuous evaluation never produces an improvement, a pass or a decision on any verdict surface (#805); synthetic data only."""
import json

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation import side_by_side as sbs
from agent_harness_lab.evaluation.plan import ExperimentSpec, schedule
from agent_harness_lab.evaluation.statistics import TrialRecord, analyze
from agent_harness_lab.evidence_label import evidence_strength
from agent_harness_lab.promotion.policy import legacy_coverage, single_run_decision
from tests.unit.evaluation.test_side_by_side_report import BASE, CAND, ab, experiment, verdicts
from tests.unit.workflow.test_evidence_label import WIN, fixture


def spec(candidate_hash='c' * 64, tasks=30):
    return ExperimentSpec('synthetic-vacuity', tuple((f't{i:03d}', f'r{i:03d}', 'family') for i in range(tasks)), 1, 13,
                          2 * tasks, 2, .05, .1, .05, (('family', 1),), 'b' * 64, candidate_hash, 'g' * 64, 'r' * 64)


def candidate_wins(plan):
    return [TrialRecord(row['trial_id'], row['version'] == 'candidate', 'graded', 'executable_grader',
                        plan.baseline_hash, plan.candidate_hash, plan.grader_hash, plan.runtime_hash) for row in schedule(plan)]


class TestLegacyCoverage:
    def test_a_manifest_with_no_tasks_is_not_covered_by_zero_trials(self):
        assert not legacy_coverage({'tasks': []}, [])

    @pytest.mark.parametrize('tasks, trials', [([], [dict(version='baseline', task='t')]), ([dict(id='t')], [])],
                             ids=['no-tasks-with-a-trial', 'a-task-without-trials'])
    def test_coverage_needs_one_trial_per_task_and_arm(self, tasks, trials):
        assert not legacy_coverage({'tasks': tasks}, trials)

    def test_one_trial_per_task_and_arm_is_covered(self):
        assert legacy_coverage({'tasks': [dict(id='t')]}, [dict(version=v, task='t') for v in ('baseline', 'candidate')])


class TestIdenticalArms:
    def test_the_fixed_analysis_refuses_arms_with_the_same_harness_hash(self):
        with pytest.raises(ValueError, match='arms_identical'):
            analyze(spec(candidate_hash='b' * 64), [])

    def test_a_plan_with_distinct_harness_hashes_still_reaches_a_supported_analysis(self):
        plan = spec()
        assert analyze(plan, candidate_wins(plan))['status'] == 'supported'

    def test_validation_names_the_label_for_arms_with_the_same_hash(self):
        with pytest.raises(ValueError, match='arms_identical'):
            spec(candidate_hash='b' * 64).validate()

    def test_the_experiment_command_exits_2_and_names_the_label_for_identical_arms(self, tmp_path, capsys):
        plan = spec(candidate_hash='b' * 64)
        path = tmp_path / 'spec.json'
        path.write_text(json.dumps(plan.to_dict()))
        with pytest.raises(SystemExit) as stopped:
            main(['experiment', '--spec', str(path), '--preflight'])
        assert stopped.value.code == 2 and 'arms_identical' in capsys.readouterr().err

    def test_side_by_side_never_calls_a_pair_improved_when_both_arms_share_a_harness_hash(self):
        trials = ab('fixed', False, True)
        for record in trials: record['harness_hash'] = BASE
        report = sbs.build(experiment(trials, candidate_hash=BASE))
        assert verdicts(report) == {('fixed', 0): ('not_comparable', ['arms_identical'])}
        assert report['summary']['verdicts']['improved'] == 0 and report['summary']['comparable_pairs'] == 0

    def test_side_by_side_with_distinct_harness_hashes_keeps_its_verdict(self):
        report = sbs.build(experiment(ab('fixed', False, True)))
        assert verdicts(report) == {('fixed', 0): ('improved', [])} and BASE != CAND


class TestVacuousDecisions:
    manifest = dict(tasks=[dict(id='a', split='development', root='r1'), dict(id='b', split='regression', root='r2')])
    trials = [dict(task=task, version=version, split=split,
                   status='failed' if version == 'baseline' and split == 'development' else 'passed')
              for task, split in (('a', 'development'), ('b', 'regression')) for version in ('baseline', 'candidate')]

    def decide(self, **options):
        from agent_harness_lab.promotion.policy import implementation_prerequisites
        return single_run_decision(self.manifest, self.trials, inputs_unchanged=True,
                                   prerequisites=implementation_prerequisites(), **options)

    def test_a_decision_without_labels_keeps_its_observed_pattern(self):
        decision = self.decide()
        assert decision['observed_pattern'] == 'candidate_passed_where_baseline_failed' and 'vacuity' not in decision

    def test_a_vacuous_run_is_invalid_evidence_with_the_labels_and_never_a_difference(self):
        decision = self.decide(vacuity=['arms_identical'])
        assert (decision['disposition'], decision['observed_pattern']) == ('invalid_evidence', 'vacuous_evaluation')
        assert 'vacuous_evaluation' in decision['reasons'] and decision['vacuity']['labels'] == ['arms_identical']
        assert decision['recommendation_authority'] is False and decision['promotion']['status'] != 'recommended'

    def test_the_evidence_strength_label_of_a_paired_win_is_insufficient_when_the_run_was_vacuous(self):
        label = evidence_strength(**fixture(**WIN), vacuity=['arms_identical'])
        assert label['label'] == 'insufficient' and label['adoption_support'] is False
        assert label['reasons'] == ['evaluation_vacuous', 'arms_identical']

    def test_the_evidence_strength_label_of_a_paired_win_is_unchanged_without_labels(self):
        assert evidence_strength(**fixture(**WIN), vacuity=())['label'] == 'behavioral_proof'
