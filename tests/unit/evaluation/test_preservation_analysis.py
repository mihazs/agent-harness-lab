"""Conditional math on public synthetic data, never qualification or native proof."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import math
import random
import unittest

from agent_harness_lab.evaluation.cohorts import MAX_BYTES
from agent_harness_lab.evaluation.plan import ExperimentSpec, schedule
from agent_harness_lab.evaluation.statistics import TrialRecord, analyze
from agent_harness_lab.promotion.policy import decide



def plan(n=1000, repeats=2):
    return ExperimentSpec('synthetic-preservation-math', tuple((f't{i:04d}', f'r{i:04d}', 'family') for i in range(n)),
        repeats, 13, 2*n*repeats, 2, .05, .1, .05, (('family', 1),), 'b'*64, 'c'*64, 'g'*64, 'r'*64,
        cohort_manifest_sha256='d'*64)


def binding(spec, members=None, margin=.15, required=True):
    members = members if members is not None else [task[0] for task in spec.tasks[:500]]
    tasks = {task: (root, family) for task, root, family in spec.tasks}
    records = [dict(task_id=task, root_id=tasks[task][0], family=tasks[task][1],
        bank_sha256='1'*64, bank_revision_sha256='2'*64, task_revision_sha256='3'*64,
        fixture_sha256='4'*64, materialized_sha256='5'*64, execution_fixture_sha256='6'*64,
        bundle_sha256='7'*64, grader_version='v1') for task in members]
    # Deliberately unresolved declarations: this tests math, not qualification.
    cohort = dict(cohort_id='protected', role='historical_pass', required=required, margin=margin,
        exposure='development_regression', severity_policy='verified_critical_veto', members=records,
        qualification=dict(history_store='/public-synthetic-unopened-authority', run_id='8'*64,
            event_sha256='9'*64, evidence_sha256='a'*64, plan_sha256='e'*64, arm='baseline',
            rule=dict(min_trials=1, min_successes=1), review=dict(identity='synthetic-reviewer',
                source='operator_attestation', basis_kind='independent_artifact_review',
                evidence_refs=[dict(ref='synthetic:unresolved', sha256='f'*64)])))
    return dict(manifest_sha256=spec.cohort_manifest_sha256,
        manifest=dict(schema_version=1, profile='preservation-cohorts-v1', author='synthetic-author', cohorts=[cohort]))


def freeze(spec, frozen=None, **options):
    frozen = deepcopy(binding(spec, **options) if frozen is None else frozen)
    raw = json.dumps(frozen['manifest'], sort_keys=True, indent=2).encode('utf-8')
    pin = hashlib.sha256(raw).hexdigest()
    frozen.update(manifest_sha256=pin, manifest_json=raw)
    return replace(spec, cohort_manifest_sha256=pin), frozen


def records(spec, outcome):
    return [TrialRecord(row['trial_id'], outcome(row), 'graded', 'executable_grader',
        spec.baseline_hash, spec.candidate_hash, spec.grader_hash, spec.runtime_hash) for row in schedule(spec)]


def successful_preservation(row):
    return int(row['task'][1:]) < 500 or row['version'] == 'candidate'


class PreservationAnalysisTests(unittest.TestCase):
    def test_exact_aggregate_gain_can_reject_preservation_loss(self):
        spec = plan()
        outcome = lambda row: (int(row['root'][1:]) < 200) == (row['version'] == 'baseline')
        spec, frozen = freeze(spec, members=[t[0] for t in spec.tasks[:200]])
        data = records(spec, outcome)
        result = analyze(spec, data, cohort_binding=frozen)
        self.assertAlmostEqual(result['primary']['mean'], .6)
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(result['reason'], 'preservation_regression')
        endpoint = result['preservation']['cohorts']['protected']
        self.assertAlmostEqual(endpoint['endpoint']['mean'], -1)
        self.assertEqual(endpoint['endpoint']['independent_roots'], 200)
        self.assertEqual(endpoint['descriptive']['transitions']['pass_fail'], 400)
        self.assertEqual(result['preservation']['error_allocation']['per_endpoint_alpha'], .05/3)
        legacy = replace(spec, cohort_manifest_sha256=None)
        self.assertEqual(analyze(legacy, records(legacy, outcome))['status'], 'supported')

    def test_preserved_500_roots_with_500_repairs_support_conditional_criteria(self):
        spec = plan()
        spec, frozen = freeze(spec)
        result = analyze(spec, records(spec, successful_preservation), cohort_binding=frozen)
        self.assertEqual(result['status'], 'supported')
        preservation = result['preservation']
        cohort = preservation['cohorts']['protected']
        self.assertEqual(cohort['disposition'], 'supported')
        self.assertEqual(cohort['endpoint']['independent_roots'], 500)
        self.assertEqual(cohort['descriptive']['transitions']['pass_pass'], 1000)
        self.assertEqual(preservation['descriptive']['overall']['transitions']['fail_pass'], 1000)
        self.assertFalse(result['publication_allowed'])
        self.assertEqual(preservation['qualification_authority'], 'not_validated_by_conditional_math')
        self.assertEqual(decide(spec, dict(result, oracle_validity='current')).status, 'blocked')

    def test_sparse_cohort_inconclusive_and_optional_is_only_descriptive(self):
        spec = plan()
        spec, frozen = freeze(spec, members=[spec.tasks[0][0]])
        data = records(spec, successful_preservation)
        result = analyze(spec, data, cohort_binding=frozen)
        self.assertEqual(result['status'], 'inconclusive')
        self.assertEqual(result['preservation']['cohorts']['protected']['disposition'], 'inconclusive')
        frozen['manifest']['cohorts'][0]['required'] = False
        spec, frozen = freeze(spec, frozen)
        result = analyze(spec, records(spec, successful_preservation), cohort_binding=frozen)
        cohort = result['preservation']['cohorts']['protected']
        self.assertEqual(cohort['disposition'], 'descriptive_only')
        self.assertIsNone(cohort['endpoint'])
        self.assertEqual(result['preservation']['error_allocation']['per_endpoint_alpha'], .05/2)

    def test_missing_invalid_unknown_and_unexpected_evidence_are_visible_before_returns(self):
        spec = plan(4, 2)
        spec, frozen = freeze(spec)
        data = records(spec, lambda row: True)
        mutations = [('missing', data[1:]), ('duplicate', data + [data[0]]),
            ('unknown', [replace(data[0], outcome=None)] + data[1:]),
            ('invalid', [replace(data[0], outcome=1)] + data[1:]),
            ('unexpected', data + [replace(data[0], trial_id='unexpected')]),
            ('provenance', [replace(data[0], runtime_hash='wrong')] + data[1:])]
        for kind, changed in mutations:
            with self.subTest(kind=kind):
                result = analyze(spec, changed, cohort_binding=frozen)
                self.assertIn(result['status'], ('blocked', 'incomplete'))
                self.assertNotIn('primary', result)
                cohort = result['preservation']['cohorts']['protected']
                self.assertIsNone(cohort['endpoint'])
                coverage = result['preservation']['descriptive']['coverage']
                self.assertEqual(coverage['planned_trials'], 16)
                overall = result['preservation']['descriptive']['overall']
                self.assertEqual(overall['planned_pairs'], 8)
                if kind not in ('unexpected',):
                    self.assertTrue(any(value['success_rate'] is None for value in overall['arms'].values()))
                if kind == 'missing':
                    self.assertEqual(coverage['missing_trials'], 1)
                    self.assertEqual(overall['transitions']['missing'], 1)
                if kind == 'duplicate':
                    self.assertEqual(coverage['duplicate_records'], 1)
                if kind == 'unknown':
                    self.assertEqual(coverage['unknown_grades'], 1)
                if kind == 'unexpected':
                    self.assertEqual(coverage['unexpected_trial_ids'], ['unexpected'])

    def test_critical_veto_survives_missing_binding_and_other_unknowns(self):
        spec = plan(4)
        spec, frozen = freeze(spec)
        data = records(spec, lambda row: True)
        critical = [replace(data[0], critical_regression=True, outcome=None)] + data[2:]
        for supplied in (None, frozen):
            result = analyze(spec, critical, cohort_binding=supplied)
            self.assertEqual((result['status'], result['reason']), ('rejected', 'critical_regression'))
            self.assertEqual(result['preservation']['descriptive']['coverage']['missing_trials'], 1)
        unverified = [replace(data[0], critical_regression=True, provenance='self_report')] + data[1:]
        self.assertEqual(analyze(spec, unverified, cohort_binding=frozen)['status'], 'incomplete')

    def test_members_pins_duplicates_and_same_analysis_qualification_fail_closed(self):
        spec = plan(4)
        spec, frozen = freeze(spec)
        data = records(spec, lambda row: True)
        mutations = [lambda value:value.update(manifest_sha256='f'*64),
            lambda value:value['manifest']['cohorts'][0]['members'].append(deepcopy(value['manifest']['cohorts'][0]['members'][0])),
            lambda value:value['manifest']['cohorts'][0]['members'][0].update(root_id='forged'),
            lambda value:value['manifest']['cohorts'][0].update(required=1),
            lambda value:value['manifest']['cohorts'][0].update(margin=float('nan')),
            lambda value:value['manifest']['cohorts'][0]['qualification'].update(plan_sha256=spec.digest)]
        self.assertEqual(analyze(spec, data)['status'], 'blocked')
        for mutate in mutations:
            value = deepcopy(frozen)
            mutate(value)
            with self.subTest(value=value):
                result = analyze(spec, data, cohort_binding=value)
                self.assertEqual(result['status'], 'blocked')
                self.assertNotIn('primary', result)
        value = deepcopy(frozen)
        value['qualification_bindings'] = {'protected': dict(trial_ids=[data[0].trial_id])}
        self.assertEqual(analyze(spec, data, cohort_binding=value)['status'], 'blocked')

    def test_shuffle_repeat_variants_and_overlapping_cohorts_keep_root_identity(self):
        spec = plan(500, 3)
        # Two tasks per original root; neither variants nor repeats add roots.
        spec = replace(spec, tasks=tuple((task, f'r{i//2:04d}', family) for i, (task, _, family) in enumerate(spec.tasks)))
        frozen = binding(spec, [task[0] for task in spec.tasks[:200]])
        overlap = deepcopy(frozen['manifest']['cohorts'][0])
        overlap['cohort_id'] = 'overlap'
        frozen['manifest']['cohorts'].append(overlap)
        spec, frozen = freeze(spec, frozen)
        data = records(spec, lambda row: row['version'] == 'candidate')
        result = analyze(spec, data, cohort_binding=frozen)
        self.assertEqual(result['roots'], 250)
        endpoints = result['preservation']['cohorts']
        self.assertEqual(endpoints['protected']['endpoint']['independent_roots'], 100)
        self.assertEqual(endpoints['protected']['endpoint'], endpoints['overlap']['endpoint'])
        self.assertEqual(result['preservation']['error_allocation']['per_endpoint_alpha'], .05/4)
        shuffled = list(data)
        random.Random(19).shuffle(shuffled)
        self.assertEqual(result, analyze(spec, shuffled, cohort_binding=frozen))
        frozen['manifest']['cohorts'].reverse()
        frozen['manifest']['cohorts'][0]['members'].reverse()
        revised, reordered = freeze(spec, frozen)
        self.assertNotEqual(spec.digest, revised.digest)
        comparison = analyze(revised, records(revised, lambda row: row['version'] == 'candidate'), cohort_binding=reordered)
        comparison['preservation']['manifest_sha256'] = spec.cohort_manifest_sha256
        self.assertEqual(result, comparison)

    def test_returned_assumptions_cannot_mutate_later_reports(self):
        spec = plan(20)
        spec, frozen = freeze(spec)
        data = records(spec, lambda row: row['version'] == 'candidate')
        first = analyze(spec, data, cohort_binding=frozen)
        expected = list(first['preservation']['assumptions'])
        first['preservation']['assumptions'].clear()
        first['preservation']['cohorts']['protected']['endpoint']['assumptions'].clear()
        following = analyze(spec, data, cohort_binding=frozen)['preservation']
        self.assertEqual(following['assumptions'], expected)
        self.assertEqual(following['cohorts']['protected']['endpoint']['assumptions'], expected)

    def test_repetitions_do_not_inflate_root_precision(self):
        first, frozen = freeze(plan(100, 1))
        second = replace(first, repeats=5, max_trials=1000)
        outcome = lambda row: row['version'] == 'candidate'
        before = analyze(first, records(first, outcome), cohort_binding=frozen)
        after = analyze(second, records(second, outcome), cohort_binding=frozen)
        self.assertEqual(before['primary'], after['primary'])
        self.assertEqual(before['preservation']['cohorts']['protected']['endpoint'],
                         after['preservation']['cohorts']['protected']['endpoint'])
        self.assertEqual(after['preservation']['descriptive']['overall']['planned_pairs'], 500)

    def test_unequal_family_sizes_keep_frozen_primary_weights(self):
        spec = plan(1000, 1)
        spec = replace(spec, tasks=tuple((task, root, 'small' if i < 100 else 'large') for i, (task, root, _) in enumerate(spec.tasks)),
                       family_weights=(('small', .8), ('large', .2)))
        outcome = lambda row: row['family'] == 'small' and row['version'] == 'candidate'
        spec, frozen = freeze(spec, members=[task[0] for task in spec.tasks[:50]], required=False)
        result = analyze(spec, records(spec, outcome), cohort_binding=frozen)
        self.assertAlmostEqual(result['primary']['mean'], .8)
        self.assertEqual(result['families']['small']['independent_roots'], 100)
        self.assertEqual(result['families']['large']['independent_roots'], 900)
        expected_radius = math.sqrt(2*math.log(2/(.05/3))*(.8**2/100 + .2**2/900))
        self.assertAlmostEqual(result['primary']['low'], .8 - expected_radius)

    def test_null_aa_baseline_selection_is_biased_and_cannot_qualify_same_analysis(self):
        spec = plan(1000, 1)
        rng = random.Random(122)
        observed = {(task, arm): rng.random() < .5 for task, _, _ in spec.tasks for arm in ('baseline', 'candidate')}
        selected = [task for task, _, _ in spec.tasks if observed[(task, 'baseline')]]
        spec, frozen = freeze(spec, members=selected)
        data = records(spec, lambda row: observed[(row['task'], row['version'])])
        result = analyze(spec, data, cohort_binding=frozen)
        endpoint = result['preservation']['cohorts']['protected']['endpoint']
        self.assertLess(endpoint['mean'], -.4)  # Selection bias despite A/A generation.
        self.assertEqual(result['preservation']['qualification_authority'], 'not_validated_by_conditional_math')
        frozen['manifest']['cohorts'][0]['qualification']['plan_sha256'] = spec.digest
        self.assertEqual(analyze(spec, data, cohort_binding=frozen)['status'], 'blocked')

    def test_changed_margin_requires_new_raw_pin_plan_and_trial_ids(self):
        spec, frozen = freeze(plan())
        data = records(spec, successful_preservation)
        self.assertEqual(analyze(spec, data, cohort_binding=frozen)['status'], 'supported')
        altered = deepcopy(frozen)
        altered['manifest']['cohorts'][0]['margin'] = .01
        self.assertEqual(analyze(spec, data, cohort_binding=altered)['status'], 'blocked')
        revised, altered = freeze(spec, altered)
        self.assertEqual(analyze(spec, data, cohort_binding=altered)['status'], 'blocked')
        self.assertNotEqual(spec.digest, revised.digest)
        next_data = records(revised, successful_preservation)
        self.assertTrue({r.trial_id for r in data}.isdisjoint(r.trial_id for r in next_data))
        self.assertEqual(analyze(revised, data, cohort_binding=altered)['status'], 'incomplete')
        self.assertEqual(analyze(revised, next_data, cohort_binding=altered)['status'], 'inconclusive')

    def test_changed_required_flag_cannot_turn_same_pinned_loss_into_support(self):
        initial = plan()
        spec, frozen = freeze(initial, members=[task[0] for task in initial.tasks[:200]])
        outcome = lambda row: (int(row['root'][1:]) < 200) == (row['version'] == 'baseline')
        data = records(spec, outcome)
        self.assertEqual(analyze(spec, data, cohort_binding=frozen)['status'], 'rejected')
        altered = deepcopy(frozen)
        altered['manifest']['cohorts'][0]['required'] = False
        self.assertEqual(analyze(spec, data, cohort_binding=altered)['status'], 'blocked')
        revised, changed = freeze(spec, altered)
        forged = deepcopy(changed)
        forged['manifest_sha256'] = spec.cohort_manifest_sha256
        self.assertEqual(analyze(spec, data, cohort_binding=forged)['status'], 'blocked')
        self.assertNotEqual(spec.digest, revised.digest)
        next_data = records(revised, outcome)
        self.assertTrue({r.trial_id for r in data}.isdisjoint(r.trial_id for r in next_data))
        self.assertEqual(analyze(revised, next_data, cohort_binding=changed)['status'], 'supported')

    def test_raw_manifest_parser_and_embedded_schema_aliases_fail_closed(self):
        spec, frozen = freeze(plan(4))
        data = records(spec, lambda row: True)
        for raw in (None, bytearray(b'{}')):
            changed = dict(frozen, manifest_json=raw)
            self.assertEqual(analyze(spec, data, cohort_binding=changed)['status'], 'blocked')
        raw_cases = [b'{', b'[]', b'\xff', b'{"schema_version":1,"schema_version":1}',
                     b'{"schema_version":NaN}', b'[' * 2000 + b']' * 2000, b' ' * (MAX_BYTES + 1)]
        for raw in raw_cases:
            pin = hashlib.sha256(raw).hexdigest()
            revised = replace(spec, cohort_manifest_sha256=pin)
            changed = dict(frozen, manifest_json=raw, manifest_sha256=pin)
            with self.subTest(raw_prefix=raw[:30]):
                self.assertEqual(analyze(revised, records(revised, lambda row: True), cohort_binding=changed)['status'], 'blocked')
        for change in (lambda value:value.update(schema_version=True),
                       lambda value:value['cohorts'][0].update(required=1)):
            changed = deepcopy(frozen)
            change(changed['manifest'])
            self.assertEqual(analyze(spec, data, cohort_binding=changed)['status'], 'blocked')
            revised, raw_alias = freeze(spec, changed)
            self.assertEqual(analyze(revised, records(revised, lambda row: True), cohort_binding=raw_alias)['status'], 'blocked')
        self.assertEqual(analyze(spec, data, cohort_binding=dict(frozen, manifest_json=frozen['manifest_json'].decode()))['status'],
                         analyze(spec, data, cohort_binding=frozen)['status'])

    def test_raw_whitespace_is_preserved_in_authority_hash(self):
        spec, frozen = freeze(plan(4))
        changed = dict(frozen, manifest_json=frozen['manifest_json'] + b' \n')
        self.assertEqual(analyze(spec, records(spec, lambda row: True), cohort_binding=changed)['status'], 'blocked')
        pin = hashlib.sha256(changed['manifest_json']).hexdigest()
        revised = replace(spec, cohort_manifest_sha256=pin)
        changed['manifest_sha256'] = pin
        result = analyze(revised, records(revised, lambda row: True), cohort_binding=changed)
        self.assertNotEqual(result['status'], 'blocked')
        self.assertEqual(result['preservation']['manifest_sha256'], pin)

    def test_legacy_outputs_match_the_frozen_original_module(self):
        import hashlib
        import json
        import sys
        # Python 3.12 changed float summation; pin each frozen baseline's actual output.
        # Output hashes captured from the frozen 122A statistics source, without Git at test time.
        expected = ['00cd0949852be50cfb867c65bee7c7f38c9c9e924f2519b40506c9ec828104da', '589f4a67b297d5fc625e6f31c87e3d09a0e51c34631ff52a798eb01faee39774', '00cd0949852be50cfb867c65bee7c7f38c9c9e924f2519b40506c9ec828104da']
        if sys.version_info < (3, 12):
            expected = ['d9869c1b6abc8fffaa38b6bb85b541eb2ad751e39a04953cf174eba47b73f262',
                        '49451c685e5d5ca6937228400540aa43ee29fb9c83a335fe07942a1534423d73',
                        'd9869c1b6abc8fffaa38b6bb85b541eb2ad751e39a04953cf174eba47b73f262']
        spec = replace(plan(40), cohort_manifest_sha256=None)
        self.assertEqual(spec.digest, '61fdfd3f156b824c1af55dbba0a3988eb726638bc7e12fb6a1700c14d380831d')
        for mode, frozen in zip((lambda row: True, lambda row: row['version'] == 'candidate', lambda row: False), expected):
            result = analyze(spec, records(spec, mode), cohort_binding={'ignored': True})
            self.assertEqual(hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest(), frozen)
        self.assertEqual(analyze(spec, []), dict(status='incomplete', reason='missing_or_duplicate_trials'))
