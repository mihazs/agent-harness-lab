"""Deterministic report-integrity fields on synthetic trials only (#696)."""
import json
import subprocess
from pathlib import Path

from agent_harness_lab.evidence_label import (MIN_BASELINE_FAILURES, evidence_strength, grader_fingerprint,
                                              pre_edit_check, seeded_provenance)
from agent_harness_lab.report_lint import lint

ROOT = Path(__file__).resolve().parents[3]
SKILL = (ROOT/'skills/harness-self-improvement/SKILL.md').read_text()
INVOCATION = (ROOT/'skills/harness-self-improvement/references/invocation.md').read_text()


def fixture(base, candidate, *, dev=2, regression=1, heldout=0, inputs_unchanged=True):
    """Synthetic manifest and trials; ``base``/``candidate`` map split -> statuses applied to every task of it."""
    counts = dict(development=dev, regression=regression, heldout=heldout)
    tasks = [dict(id=f'{split}-{i}', split=split) for split, n in counts.items() for i in range(n)]
    trials = [dict(task=t['id'], split=t['split'], version=version, status=(statuses[t['split']] if isinstance(statuses[t['split']], str)
              else statuses[t['split']][int(t['id'][-1])])) for t in tasks for version, statuses in (('baseline', base), ('candidate', candidate))]
    return dict(manifest=dict(tasks=tasks), trials=trials, inputs_unchanged=inputs_unchanged)


WIN = dict(base=dict(development='failed', regression='passed', heldout='passed'),
           candidate=dict(development='passed', regression='passed', heldout='passed'))


class TestEvidenceStrength:
    def test_failing_baselines_paired_win_and_a_control_is_behavioral_proof(self):
        label = evidence_strength(**fixture(**WIN))
        assert label['label'] == 'behavioral_proof' and label['reasons'] == []
        assert label['criteria']['baseline_failures'] == 2 >= MIN_BASELINE_FAILURES
        assert label['controls_run'] == ['adjacent_regression'] and label['controls_not_run'] == ['transfer']
        assert label['adoption_support'] is True and label['model_judgment'] is False

    def test_baseline_failures_below_the_minimum_have_no_behavioral_proof(self):
        label = evidence_strength(**fixture(**WIN, dev=1))
        assert label['label'] == 'source_correction_without_behavioral_proof'
        assert label['reasons'] == ['baseline_failures_below_minimum']
        assert label['adoption_support'] is False

    def test_baseline_that_never_reached_a_verdict_did_not_exercise_the_target(self):
        label = evidence_strength(**fixture(WIN['base'] | dict(development='execution_error'), WIN['candidate']))
        assert label['label'] == 'source_correction_without_behavioral_proof'
        assert 'baseline_did_not_exercise_target' in label['reasons']
        assert label['criteria']['baseline_exercised_target'] is False

    def test_missing_control_says_regressions_not_tested_and_lists_controls_not_run(self):
        label = evidence_strength(**fixture(**WIN, regression=0))
        assert label['label'] == 'source_correction_without_behavioral_proof'
        assert 'no_regression_or_transfer_control_ran' in label['reasons']
        assert label['regression_statement'] == 'regressions not tested'
        assert label['controls_not_run'] == ['adjacent_regression', 'transfer']

    def test_a_transfer_control_alone_supports_the_label_but_not_a_no_regression_claim(self):
        label = evidence_strength(**fixture(**WIN, regression=0, heldout=1))
        assert label['label'] == 'behavioral_proof'
        assert label['regression_statement'] == 'regressions not tested'
        assert label['controls_not_run'] == ['adjacent_regression']

    def test_changed_inputs_or_a_stale_attempt_are_insufficient_even_with_a_paired_win(self):
        assert evidence_strength(**fixture(**WIN, inputs_unchanged=False))['reasons'] == ['frozen_inputs_changed']
        stale = evidence_strength(**fixture(**WIN), final_attempt=False)
        assert stale['label'] == 'insufficient' and stale['adoption_support'] is False

    def test_a_void_comparison_never_reports_a_passing_regression_control_as_valid(self):
        for options in (dict(inputs_unchanged=False), dict(final_attempt=False), dict(grader_changed=True)):
            label = evidence_strength(**{**fixture(**WIN), **options})
            assert label['label'] == 'insufficient' and label['adoption_support'] is False
            assert label['regression_statement'] == 'regressions not tested'
            assert label['controls_run'] == [] and label['controls_not_run'] == ['adjacent_regression', 'transfer']
            assert label['criteria']['regression_or_transfer_control_ran'] is False

    def test_grader_change_blocks_adoption_support_and_names_the_reason(self):
        label = evidence_strength(**fixture(**WIN), grader_changed=True)
        assert label['reasons'] == ['grader_changed_after_trials']
        assert label['comparison_valid_for_adoption'] is False and label['adoption_support'] is False

    def test_candidate_failure_or_regression_is_insufficient_and_unknown_stays_unknown(self):
        worse = evidence_strength(**fixture(WIN['base'], WIN['candidate'] | dict(regression='failed')))
        assert worse['label'] == 'insufficient' and worse['regression_statement'] == 'regression observed'
        unknown = evidence_strength(**fixture(WIN['base'], WIN['candidate'] | dict(heldout='execution_error'), heldout=1))
        assert unknown['label'] == 'insufficient' and 'candidate_arm_not_all_passed' in unknown['reasons']

    def test_absent_trials_are_insufficient_not_proof(self):
        label = evidence_strength(None, None, inputs_unchanged=True)
        assert label['label'] == 'insufficient' and label['reasons'] == ['no_covered_evaluation']


class TestSeededProvenance:
    def test_hint_is_recorded_by_digest_only_and_forecloses_independence(self):
        record = seeded_provenance('prefer the secret-handling rule', 'focus')
        assert record['agent_supplied_hypothesis'][0]['source'] == 'focus'
        assert 'secret-handling' not in json.dumps(record)
        assert record['candidate']['independent_corroboration'] is False
        assert seeded_provenance('x', 'from_audit')['agent_supplied_hypothesis'][0]['source'] == 'from_audit'

    def test_unseeded_run_claims_no_independence(self):
        record = seeded_provenance(None, 'focus')
        assert record['agent_supplied_hypothesis'] == [] and 'candidate' not in record


class TestGraderBoundary:
    def test_editing_a_grader_file_or_a_self_test_changes_the_fingerprint(self, tmp_path):
        (tmp_path/'grade.py').write_text('assert True\n')
        path = tmp_path/'manifest.json'
        path.write_text(json.dumps(dict(tasks=[dict(id='a', grader=['python3', 'grade.py', '{workspace}'])], grader_self_tests=[])))
        first = grader_fingerprint(path)
        assert first == grader_fingerprint(path)
        (tmp_path/'grade.py').write_text('assert False\n')
        assert grader_fingerprint(path) != first
        assert grader_fingerprint(tmp_path/'missing.json') is None


class TestPreEditCheck:
    def git(self, root, *args):
        subprocess.run(['git', '-C', str(root), '-c', 'user.name=t', '-c', 'user.email=t@example.test', *args], check=True, capture_output=True)

    def test_reports_touching_clean_and_not_checked_without_modifying_the_tree(self, tmp_path):
        self.git(tmp_path, 'init', '-q')
        (tmp_path/'AGENTS.md').write_text('a')
        (tmp_path/'other.md').write_text('o')
        self.git(tmp_path, 'add', '.')
        self.git(tmp_path, 'commit', '-qm', 'init')
        assert pre_edit_check(tmp_path, ['AGENTS.md'])['uncommitted_changes'] == 'clean'
        (tmp_path/'AGENTS.md').write_text('changed')
        found = pre_edit_check(tmp_path, ['AGENTS.md'])
        assert (found['uncommitted_changes'], found['uncommitted_entries'], found['open_pull_requests']) == ('touching', 1, 'not_checked')
        assert pre_edit_check(tmp_path, ['other.md'])['uncommitted_changes'] == 'clean'
        assert (tmp_path/'AGENTS.md').read_text() == 'changed'

    def test_non_git_directory_is_not_checked(self, tmp_path):
        found = pre_edit_check(tmp_path/'missing', ['AGENTS.md'])
        assert found['uncommitted_changes'] == 'not_checked' and found['read_only'] is True


class TestReportLint:
    receipt = dict(evidence_strength=dict(criteria=dict(baseline_failures=2, minimum_baseline_failures=2)), elapsed_seconds=12.5, rate=0.5)

    def test_numbers_copied_from_a_receipt_field_pass(self):
        report = ('Baseline failed 2 [receipt: evidence_strength.criteria.baseline_failures] trials; the minimum is '
                  '2 [receipt: evidence_strength.criteria.minimum_baseline_failures]. Run took 12.5s [receipt: elapsed_seconds] '
                  'at 50% [receipt: rate]; prior sessions: unknown. See #696 and 0.176.0, `ahl improve .` in 2026-11-12.\n1. item\n')
        assert lint(report, self.receipt) == []

    def test_a_number_without_a_receipt_source_fails(self):
        problems = lint('The run took 14s and failed 3 trials.', self.receipt)
        assert len(problems) == 2 and 'no [receipt: path] source' in problems[0]

    def test_a_number_that_disagrees_with_its_cited_field_or_cites_nothing_fails(self):
        assert 'does not match' in lint('Failed 3 [receipt: evidence_strength.criteria.baseline_failures].', self.receipt)[0]
        assert 'does not match' in lint('Failed 2 [receipt: no.such.field].', self.receipt)[0]


class TestReportingRules:
    def test_skill_forbids_calling_a_seeded_match_independent(self):
        text = SKILL + INVOCATION
        for phrase in ('agent_supplied_hypothesis', 'independent_corroboration', 'never describe a match as an independent finding'):
            assert phrase in text.lower().replace('\n', ' ') or phrase in text

    def test_skill_quotes_labels_and_receipt_rules_without_growing_the_entrypoint(self):
        text = SKILL + INVOCATION
        for phrase in ('behavioral_proof', 'source_correction_without_behavioral_proof', 'regressions not tested',
                       'grader_changed_after_trials', 'outside_budget_unverified', 'not_checked', '[receipt:'):
            assert phrase in text, phrase
        assert len(SKILL.splitlines()) <= 200
