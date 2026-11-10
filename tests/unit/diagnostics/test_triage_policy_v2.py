"""Synthetic contracts for the schema v2 multi-class triage policy (validation role only)."""
from copy import deepcopy
import hashlib
import json
import unittest

from agent_harness_lab.diagnostics import triage_policy as triage
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from tools import benchmark_models as bench

try:
    from tests.unit.evaluation import test_benchmark_contracts as benchmark_fixtures
except ImportError:
    from tests.unit.evaluation import test_benchmark_contracts as benchmark_fixtures

SEVERE, NORMAL, NONE = 'tool_misuse', 'instruction_drift', 'no_incident'


def policy(**changes):
    value = dict(schema_version=2, profile='diagnostic-triage-policy-v2',
        classes={SEVERE: dict(severity='severe', threshold=.6, min_precision=.5, min_recall=.5),
                 NORMAL: dict(severity='normal', threshold=.6, min_precision=.5, min_recall=.5),
                 NONE: dict(severity='none', threshold=.6, min_precision=.5, min_recall=.5)},
        costs=dict(false_alert=1, wrong_class=2, missed_alert=5, severe_miss=50, review=1),
        review_capacity_roots=10, abstention=dict(min_margin=.1), min_coverage=.5, max_severe_misses=0,
        threshold_provenance='human_preregistered', model_sha256=None, calibrator_sha256=None,
        fit_roots_sha256=[], calibration_roots_sha256=[], threshold_tuning_roots_sha256=[])
    value.update(changes)
    return value


def row(root, label, top, score=.9, **others):
    scores = {SEVERE: 0., NORMAL: 0., NONE: 0., **others}
    if top is not None:
        scores[top] = score
    return dict(root_case_id=root, split='heldout', label=label, scores=scores)


def sha(root):
    return hashlib.sha256(root.encode()).hexdigest()


class TriagePolicyV2Tests(unittest.TestCase):
    def test_class_wise_root_grouped_confusion_and_costs(self):
        rows = [row('s1', SEVERE, SEVERE), row('s2', SEVERE, NORMAL), row('n1', NORMAL, NORMAL),
                row('n2', NORMAL, NONE), row('z1', NONE, NONE), row('z2', NONE, SEVERE),
                row('z3', NONE, NONE), row('u1', None, NONE)]
        rows += [row('z1', NONE, NONE)] * 5  # repeated rows of one root count once
        result = triage.evaluate(policy(), rows)
        self.assertEqual((result['labeled_roots'], result['unknown_roots'], result['coverage']), (7, 1, 1))
        self.assertEqual(result['confusion'][SEVERE], {SEVERE: 1, NORMAL: 1, NONE: 0, 'abstained': 0})
        self.assertEqual(result['confusion'][NONE], {SEVERE: 1, NORMAL: 0, NONE: 2, 'abstained': 0})
        severe = result['classes'][SEVERE]
        self.assertEqual((severe['tp'], severe['fp'], severe['fn'], severe['tn'], severe['abstained']), (1, 1, 1, 4, 0))
        self.assertEqual((severe['precision'], severe['recall']), (.5, .5))
        self.assertEqual(result['classes'][NONE]['tn'], 7-2-1-1)
        self.assertEqual(result['costs']['counts'],
                         dict(false_alert=1, wrong_class=0, missed_alert=1, severe_miss=1, review=0))
        self.assertEqual(result['costs']['total'], 1+5+50)
        self.assertEqual((result['qualification'], result['certification']), ('descriptive_only', 'not_issued'))
        self.assertFalse(result['automatic_promotion'])
        self.assertEqual((result['role'], result['threshold_selection']), ('validate', 'refused_in_validation'))

    def test_severe_misses_stay_visible_when_aggregate_metrics_are_high(self):
        rows = [row(f'z{i}', NONE, NONE) for i in range(200)] + [row(f'n{i}', NORMAL, NORMAL) for i in range(50)]
        rows += [row(f's{i}', SEVERE, SEVERE) for i in range(20)] + [row('hidden-severe', SEVERE, NONE)]
        result = triage.evaluate(policy(), rows)
        self.assertGreater(result['classes'][SEVERE]['recall'], .9)
        self.assertEqual(result['coverage'], 1)
        misses = result['severe_misses']
        self.assertEqual((misses['count'], misses['predicted_non_severe'], misses['unreviewed_abstention']), (1, 1, 0))
        self.assertEqual(misses['roots_sha256'], [sha('hidden-severe')])
        self.assertIn('severe_misses_above_maximum', result['reasons'])
        self.assertEqual(result['status'], 'rejected')
        self.assertNotIn('hidden-severe', json.dumps(result))
        # A wrong severe-class alert is a wrong class, not a severe miss.
        other = dict(policy()['classes'], second=dict(severity='severe', threshold=.6, min_precision=0, min_recall=0))
        wrong = [dict(r, scores=dict(r['scores'], second=0.)) for r in (row('s', SEVERE, SEVERE), row('t', SEVERE, None))]
        wrong[1]['scores']['second'] = .9
        result = triage.evaluate(policy(classes=other), wrong)
        self.assertEqual((result['severe_misses']['count'], result['costs']['counts']['wrong_class']), (0, 1))

    def test_review_capacity_overflow_turns_severe_abstentions_into_misses(self):
        rows = [row('a', SEVERE, SEVERE, .55), row('b', SEVERE, SEVERE, .5), row('c', NONE, NONE, .4),
                row('d', NONE, NONE), row('e', NORMAL, NORMAL), row('f', SEVERE, SEVERE)]
        result = triage.evaluate(policy(review_capacity_roots=1), rows)
        abstentions = result['abstentions']
        self.assertEqual((abstentions['total'], abstentions['routed_to_review'], abstentions['capacity_overflow']), (3, 1, 2))
        self.assertEqual(abstentions['by_reason'], {'below_class_threshold': 3})
        decisions = {d['root_sha256']: d for d in result['decisions']}
        self.assertTrue(decisions[sha('a')]['review'])  # label-free queue: highest severe score first
        self.assertFalse(decisions[sha('b')]['review'])
        self.assertEqual(result['severe_misses']['roots_sha256'], [sha('b')])
        self.assertEqual(result['severe_misses']['unreviewed_abstention'], 1)
        self.assertEqual(result['classes'][SEVERE]['abstained'], 2)
        self.assertEqual(result['classes'][SEVERE]['recall'], 1/3)
        self.assertIn('review_capacity_exceeded', result['reasons'])
        self.assertEqual(result['costs']['counts']['review'], 1)
        enough = triage.evaluate(policy(review_capacity_roots=3), rows)
        self.assertEqual(enough['severe_misses']['count'], 0)
        self.assertNotIn('review_capacity_exceeded', enough['reasons'])
        self.assertIn(f'class:{SEVERE}:recall_below_minimum', enough['reasons'])  # abstentions stay in recall

    def test_abstention_rules_and_all_abstain_cannot_qualify(self):
        tied = row('t', SEVERE, SEVERE, .9, **{NORMAL: .9})
        narrow = row('m', NORMAL, NORMAL, .9, **{NONE: .85})
        conflict = [row('c', NONE, NONE), row('c', NONE, NORMAL)]
        result = triage.evaluate(policy(), [tied, narrow, *conflict])
        self.assertEqual(result['abstentions']['by_reason'],
                         {'conflicting_root_decisions': 1, 'margin_below_minimum': 1, 'tied_top_classes': 1})
        self.assertEqual(result['coverage'], 0)
        self.assertIn('coverage_below_minimum', result['reasons'])
        self.assertIn(f'class:{SEVERE}:precision_undefined', result['reasons'])
        self.assertEqual(result['status'], 'rejected')
        missing = triage.evaluate(policy(), [row('z', NONE, NONE)])
        self.assertIn(f'class:{SEVERE}:no_validation_roots', missing['reasons'])
        self.assertIsNone(missing['classes'][SEVERE]['recall'])
        self.assertNotEqual(missing['status'], 'accepted_for_shadow')

    def test_validation_never_selects_thresholds_or_reuses_role_roots(self):
        rows = [row('p', SEVERE, SEVERE), row('q', NONE, NONE)]
        unset = deepcopy(policy()); unset['classes'][SEVERE]['threshold'] = None
        with self.assertRaisesRegex(ValueError, 'never selects thresholds'):
            triage.evaluate(unset, rows)
        for provenance in ('selected_on_validation', 'fitted', None):
            with self.subTest(provenance=provenance), self.assertRaisesRegex(ValueError, 'never selects'):
                triage.evaluate(policy(threshold_provenance=provenance), rows)
        for role in triage.ROLE_ROOTS:
            with self.subTest(role=role), self.assertRaisesRegex(ValueError, role):
                triage.evaluate(policy(**{role: [sha('q')]}), rows)
        self.assertFalse(hasattr(triage, 'choose_threshold'))

    def test_invalid_policy_and_observations_refuse(self):
        rows = [row('p', SEVERE, SEVERE)]
        bad_class = deepcopy(policy()); bad_class['classes'][SEVERE]['severity'] = 'critical'
        nan = deepcopy(policy()); nan['classes'][NONE]['threshold'] = float('nan')
        for value in (dict(policy(), schema_version=1), dict(policy(), extra=1), policy(costs=dict(false_alert=1)),
                      policy(review_capacity_roots=True), policy(max_severe_misses=-1), policy(model_sha256='x'),
                      policy(classes={SEVERE: policy()['classes'][SEVERE]}), bad_class, nan,
                      policy(fit_roots_sha256=[sha('a'), sha('a')]), policy(abstention={})):
            with self.subTest(value=value), self.assertRaises(ValueError):
                triage.evaluate(value, rows)
        cases = ([dict(rows[0], split='calibration')], [dict(rows[0], label='unknown_class')],
                 [dict(rows[0], scores={SEVERE: .9})], [row('p', SEVERE, SEVERE, float('nan'))],
                 [row('p', SEVERE, SEVERE, 1.5)], [row('p', SEVERE, SEVERE), row('p', NONE, SEVERE)],
                 [dict(rows[0], root_case_id='')], [])
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                triage.evaluate(policy(), value)

    def test_class_names_cannot_collide_with_abstention_outcomes(self):
        for name in ('abstained:x', 'abstained', 'a:b'):
            with self.subTest(name=name):
                classes = {name if key == SEVERE else key: spec for key, spec in policy()['classes'].items()}
                scores = {name: .9, NORMAL: .1, NONE: 0.}
                rows = [dict(root_case_id='p', split='heldout', label=name, scores=scores)]
                with self.assertRaisesRegex(ValueError, 'invalid triage class name'):
                    triage.evaluate(policy(classes=classes), rows)

    def test_streamed_rows_match_lists_and_labels_cannot_change_decisions(self):
        rows = [row(f'r{i}', (SEVERE, NORMAL, NONE)[i % 3], (SEVERE, NONE, NORMAL)[i % 3], .5+i % 5/10) for i in range(30)]
        listed = triage.evaluate(policy(), rows)
        self.assertEqual(triage.evaluate(policy(), (r for r in rows)), listed)
        relabeled = triage.evaluate(policy(), (dict(r, label=NONE) for r in rows))
        self.assertNotEqual(relabeled['confusion'], listed['confusion'])
        self.assertEqual(relabeled['decisions'], listed['decisions'])
        self.assertEqual(relabeled['policy_sha256'], listed['policy_sha256'])

    def test_reports_and_policies_are_private_even_with_forged_provenance(self):
        result = triage.evaluate(policy(), [row('p', SEVERE, SEVERE)])
        self.assertFalse(result['publication_allowed'])
        for forged in (result, {**result, **benchmark_fixtures.record(), 'publication_allowed': True},
                       {**policy(), **benchmark_fixtures.record()}):
            self.assertFalse(public_export_allowed([forged]))

    def test_v1_binary_policy_is_unchanged_and_rejects_v2(self):
        rows = [dict(root_case_id=f'r{i}', label=[True, False, None][i % 3], split='heldout', text='synthetic')
                for i in range(12)] + [dict(root_case_id='r0', label=True, split='heldout', text='synthetic')]
        scores = [(i*7 % 10)/10 for i in range(12)]+[.2]
        v1 = dict(schema_version=1, threshold=.6, min_precision=.5, min_recall=.5, min_coverage=.5)
        result = bench.evaluate_triage_policy(rows, scores, v1)
        self.assertEqual(hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest(),
                         '8866d894c3eeae5eb128294530bb8f81b2c53285fce0aeb278a08f7297ae02bc')
        with self.assertRaises(ValueError):
            bench.evaluate_triage_policy(rows, scores, policy())
        with self.assertRaises(ValueError):
            triage.evaluate(v1, [])
