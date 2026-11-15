"""Frozen predictions reconcile against outcomes without fabricating support (#10)."""
from dataclasses import replace
import unittest

from agent_harness_lab.diagnostics.predictions import PredictionLedger, PredictionRecord, reconcile


def prediction(**changes):
    base = dict(prediction_id='p1', mechanism_id='m1', baseline_hash='b1', candidate_hash='c1', bank_version='bank1',
                grader_version='g1', kind='deterministic_boundary', observable_effect='recovers on dev task',
                competing_explanation='grader defect', falsifier='dev task still fails', criterion='fail_to_pass',
                development_task_ids=('dev',), control_task_ids=('ctl',), regression_risks=('control regresses',))
    base.update(changes)
    return PredictionRecord(**base)


def pairs(dev=(False, True), ctl=(True, True), extra=()):
    rows = [dict(task_id='dev', root_id='r', repeat=0, baseline_pass=dev[0], candidate_pass=dev[1]),
            dict(task_id='ctl', root_id='r', repeat=0, baseline_pass=ctl[0], candidate_pass=ctl[1])]
    return rows + list(extra)


def obs(**changes):
    base = dict(candidate_hash='c1', bank_version='bank1', grader_version='g1', runtime_valid=True,
                grader_valid=True, pairs=pairs())
    base.update(changes)
    return base


def run(record, observation, **register):
    ledger = PredictionLedger()
    ledger.register(record, **register)
    row = reconcile(ledger, {} if observation is None else {record.prediction_id: observation})['entries'][0]
    return row['status'], row['reason']


class PredictionTests(unittest.TestCase):
    def test_outcome_statuses(self):
        self.assertEqual(run(prediction(), obs()), ('supported_under_test', 'fail_to_pass_observed'))
        self.assertEqual(run(prediction(), obs(pairs=pairs(ctl=(True, False)))), ('contradicted_under_test', 'control_regressed'))
        self.assertEqual(run(prediction(), obs(pairs=pairs(dev=(False, False)))), ('contradicted_under_test', 'effect_not_observed'))
        self.assertEqual(run(prediction(), obs(pairs=pairs(dev=(True, True)))), ('inconclusive', 'non_discriminative'))
        self.assertEqual(run(prediction(), None), ('not_tested', 'no_outcome_observed'))

    def test_missing_duplicate_and_unknown_outcomes_are_never_support(self):
        self.assertEqual(run(prediction(), obs(pairs=pairs()[:1])), ('inconclusive', 'missing_task_outcomes'))
        self.assertEqual(run(prediction(), obs(pairs=pairs(extra=pairs()[:1]))), ('not_comparable', 'duplicate_outcome'))
        bad = pairs(); bad[0]['candidate_pass'] = None
        self.assertEqual(run(prediction(), obs(pairs=bad)), ('inconclusive', 'unknown_outcome'))
        self.assertEqual(run(prediction(), obs(pairs=None)), ('inconclusive', 'no_paired_outcomes'))

    def test_stale_late_or_invalid_evaluator_is_not_comparable(self):
        self.assertEqual(run(prediction(), obs(candidate_hash='c2')), ('not_comparable', 'stale_identity'))
        self.assertEqual(run(prediction(), obs(grader_version='g2')), ('not_comparable', 'stale_identity'))
        self.assertEqual(run(prediction(), obs(), outcomes_read=['c1']), ('not_comparable', 'late_registration'))
        self.assertEqual(run(prediction(), obs(grader_valid=False))[0], 'not_comparable')
        self.assertEqual(run(prediction(), obs(runtime_valid=None))[0], 'not_comparable')

    def test_broken_grader_routes_to_evaluator_not_agent(self):
        ledger = PredictionLedger(); ledger.register(prediction())
        row = reconcile(ledger, {'p1': obs(grader_valid=False)})['entries'][0]
        self.assertEqual(row['route'], 'evaluator_or_runtime_defect')

    def test_stochastic_claim_needs_paired_analysis(self):
        record = prediction(kind='stochastic_performance')
        self.assertEqual(run(record, obs())[0], 'inconclusive')
        self.assertEqual(run(record, obs(paired_analysis='supports'))[0], 'supported_under_test')

    def test_every_prediction_stays_in_the_report_including_wrong_ones(self):
        ledger = PredictionLedger()
        ledger.register(prediction()); ledger.register(prediction(prediction_id='p2', candidate_hash='c2'))
        report = reconcile(ledger, {'p1': obs(pairs=pairs(dev=(False, False)))})
        self.assertEqual(report['eligible_predictions'], 2)
        self.assertEqual(report['counts']['contradicted_under_test'], 1)
        self.assertEqual(report['counts']['not_tested'], 1)
        self.assertFalse(report['certifies'])

    def test_frozen_records_are_immutable_and_append_only(self):
        ledger = PredictionLedger(); first = ledger.register(prediction())
        self.assertEqual(ledger.register(prediction()), first)
        with self.assertRaises(ValueError):
            ledger.register(prediction(falsifier='a different falsifier'))
        with self.assertRaises(AttributeError):
            prediction().falsifier = 'x'
        ledger.register(prediction(prediction_id='p2'))
        self.assertTrue(ledger.verify())
        ledger.receipts[0]['preregistered'] = False
        self.assertFalse(ledger.verify())

    def test_validation_needs_falsifier_and_blocks_protected_leakage(self):
        for bad in (dict(falsifier=' '), dict(competing_explanation=''), dict(kind='other'), dict(development_task_ids=()),
                    dict(control_task_ids=('dev',))):
            with self.assertRaises(ValueError):
                PredictionLedger().register(prediction(**bad))
        with self.assertRaises(ValueError):
            PredictionLedger().register(prediction(), protected_task_ids=['dev'])
        with self.assertRaises(ValueError):
            PredictionLedger().register(prediction(evidence_refs=('x',)), allowed_refs=['y'])
        PredictionLedger().register(prediction(protected_properties=('family: recovery',)), protected_task_ids=['hidden'])

    def test_composed_patch_attribution_is_unresolved_without_ablation(self):
        ledger = PredictionLedger()
        ledger.register(prediction(components=('a', 'b')))
        ledger.register(replace(prediction(prediction_id='p2', components=('a', 'b')), ablation_design='drop-one'))
        rows = {r['prediction_id']: r['attribution'] for r in reconcile(ledger, {})['entries']}
        self.assertEqual(rows, {'p1': 'unresolved', 'p2': 'single_or_isolated'})


class RevisionChainTests(unittest.TestCase):
    def chain(self):
        ledger = PredictionLedger()
        first = ledger.register(prediction())
        second = ledger.register(prediction(prediction_id='p2', supersedes='p1', candidate_hash='c2', criterion='fail_to_pass v2'))
        return ledger, first, second

    def test_revision_links_receipts_and_keeps_the_superseded_prediction_listed(self):
        ledger, first, second = self.chain()
        self.assertEqual((first['revision'], first['supersedes']), (1, None))
        self.assertEqual((second['revision'], second['supersedes']), (2, 'p1'))
        self.assertEqual(second['previous_receipt_id'], first['receipt_id'])
        self.assertTrue(ledger.verify())
        report = reconcile(ledger, {'p1': obs(pairs=pairs(dev=(False, False))), 'p2': obs(candidate_hash='c2')})
        rows = {r['prediction_id']: r for r in report['entries']}
        self.assertEqual((rows['p1']['status'], rows['p1']['superseded_by']), ('contradicted_under_test', 'p2'))
        self.assertEqual((rows['p2']['status'], rows['p2']['supersedes'], rows['p2']['revision']), ('supported_under_test', 'p1', 2))
        self.assertEqual((report['eligible_predictions'], report['superseded_predictions']), (2, 1))
        self.assertEqual(report['counts']['contradicted_under_test'], 1)

    def test_a_revision_does_not_inherit_the_superseded_outcome(self):
        ledger, _, _ = self.chain()
        row = reconcile(ledger, {'p1': obs()})['entries'][1]
        self.assertEqual((row['prediction_id'], row['status']), ('p2', 'not_tested'))

    def test_revision_after_the_candidate_outcomes_were_read_is_late(self):
        ledger = PredictionLedger(); ledger.register(prediction())
        late = ledger.register(prediction(prediction_id='p2', supersedes='p1', criterion='rewritten after the result'),
                               outcomes_read=['c1'])
        self.assertFalse(late['preregistered'])
        self.assertEqual(reconcile(ledger, {'p2': obs()})['entries'][1]['reason'], 'late_registration')

    def test_a_revision_must_be_a_real_linear_change_on_the_same_mechanism(self):
        ledger, _, _ = self.chain()
        for bad in (dict(prediction_id='p3', supersedes='missing', criterion='x'),
                    dict(prediction_id='p3', supersedes='p1', criterion='fork'),
                    dict(prediction_id='p3', supersedes='p2', mechanism_id='m2', criterion='x'),
                    dict(prediction_id='p3', supersedes='p2', candidate_hash='c2', criterion='fail_to_pass v2'),
                    dict(prediction_id='p3', supersedes='p3', criterion='x')):
            with self.assertRaises(ValueError, msg=bad):
                ledger.register(prediction(**bad))
        ledger.register(prediction(prediction_id='p3', supersedes='p2', candidate_hash='c3'))
        self.assertEqual(ledger.receipts[-1]['revision'], 3)

    def test_registering_a_revision_again_is_idempotent_and_tampering_is_detected(self):
        ledger, _, second = self.chain()
        again = ledger.register(prediction(prediction_id='p2', supersedes='p1', candidate_hash='c2', criterion='fail_to_pass v2'))
        self.assertEqual(again, second)
        self.assertEqual(len(ledger.receipts), 2)
        ledger.receipts[1]['supersedes'] = None
        self.assertFalse(ledger.verify())


if __name__ == '__main__':
    unittest.main()
