import unittest
from agent_harness_lab.evaluation.benchmark import validate_pair,public_export_allowed,measured_cost,summary,render


def runtime(model='synthetic'):
    return dict(provider='local',runtime='fake',model_revision=model,reasoning='none',task_bank_revision='v1',
                sandbox_image='fixture-v1',permissions='offline',controller_version='v1',effective_settings_verified=True)


def record(model='synthetic',source='synthetic',status='not_run'):
    return dict(baseline_runtime=runtime(model),candidate_runtime=runtime(model),status=status,execution_kind='fake_agent',
                provenance=dict(source_kind=source,approved_publication=True,source_ref='synthetic fixture',derived_from_private=False))


class BenchmarkContracts(unittest.TestCase):
    def test_mismatched_and_unverified_runtime_blocks(self):
        with self.assertRaises(ValueError): validate_pair(runtime(),runtime('other-model'))
        candidate=runtime(); candidate['effective_settings_verified']=False
        self.assertEqual(validate_pair(runtime(),candidate)['status'],'blocked')
        self.assertEqual(validate_pair(runtime(),candidate,strict=False)['status'],'unverified')

    def test_private_and_mixed_provenance_cannot_export_even_aggregates(self):
        self.assertTrue(public_export_allowed([record()]))
        for records in ([record(source='private')],[record(),record(source='private')],[]):
            self.assertFalse(public_export_allowed(records))
            with self.assertRaises(ValueError): summary(records,public=True)
        derived=record(); derived['provenance']['derived_from_private']=True
        self.assertFalse(public_export_allowed([derived]))

    def test_reports_keep_failures_abstentions_strata_and_escape_html(self):
        records=[record(status='failed'),record(status='inconclusive'),record(model='<script>',status='skipped')]
        result=summary(records,public=True)
        self.assertEqual(len(result['strata']),2)
        self.assertIsNone(result['pooled_treatment_effect'])
        self.assertEqual(result['strata'][0]['capability_claim'],'synthetic_or_replay_only')
        markdown,html=render(result)
        self.assertIn('inconclusive',markdown); self.assertIn('skipped',html)
        self.assertNotIn('<script>',html); self.assertIn('&lt;script&gt;',html)

    def test_private_controller_claims_cannot_be_relabelled_public(self):
        for fields in (dict(publication_allowed=False),dict(publication_allowed=None),
                dict(controller_run_id='1'*64),dict(cohort_manifest_sha256='2'*64),
                dict(preservation={}),dict(receipt_sha256='3'*64),
                dict(algorithm='private-preservation-decision-v1'),
                dict(receipt=dict(algorithm='private-preservation-decision-v1')),
                dict(analysis=dict(controller_run_id='4'*64)),
                dict(context=dict(algorithm='guarded-preservation-search-v1')),
                dict(algorithm='corrected-cohort-qualification-v1'),
                dict(cohort_binding={}),dict(controller_qualification={}),dict(qualification_sha256=None),
                dict(controller_grade={}),dict(trajectory_profile='controller-trajectory-v1'),
                dict(trajectory_analysis={}),dict(policy_sha256='5'*64),
                dict(receipt=dict(profile='controller-trajectory-v1')),
                dict(report_kind='controller_trajectory'),dict(context=dict(outcome_mapping='artifact_and_required_v1')),
                dict(evidence_source='controller_verified_development_only')):
            item=dict(record(),publication_allowed=True)
            item.update(fields)
            with self.subTest(fields=fields):
                self.assertFalse(public_export_allowed([item]))
                with self.assertRaises(ValueError): summary([item],public=True)

    def test_interchange_preserves_existing_private_controller_export_guards(self):
        claims=[dict.fromkeys([key]) for key in (
            'protected_access','protected_access_sha256','controller_source_profile',
            'source_capture','live_verification','verified_execution','import_sha256',
            'foreign_claims','proposal_id')]
        claims.extend(dict(profile=profile) for profile in (
            'protected-diagnostic-access-v1','ahl-controller-live-v1','external-evaluation-data-v1'))
        claims.extend(dict(format=profile) for profile in (
            'promptfoo-cases-json-v1','promptfoo-results-jsonl-v1','openai-evals-match-jsonl-v1'))
        for claim in claims:
            for wrapper in (None,'receipt','analysis','context','binding'):
                item=record()
                item.update(claim if wrapper is None else {wrapper:claim})
                with self.subTest(claim=claim,wrapper=wrapper):
                    self.assertFalse(public_export_allowed([item]))

    def test_cost_is_unknown_without_measured_usage_or_dated_pricing(self):
        self.assertIsNone(measured_cost({'input_tokens':100}))
        prices=dict(as_of='2026-10-31',currency='test-units',input_per_token=1,output_per_token=2)
        self.assertIsNone(measured_cost({'input_tokens':100},prices))
        self.assertEqual(measured_cost({'input_tokens':2,'output_tokens':3},prices)['amount'],8)
        with self.assertRaises(ValueError): measured_cost({},dict(as_of='unknown'))

    def test_treatment_harness_hashes_can_differ_within_matched_runtime(self):
        baseline=runtime(); candidate=runtime()
        baseline['harness_fingerprint']='baseline-hash'
        candidate['harness_fingerprint']='candidate-hash'
        self.assertEqual(validate_pair(baseline,candidate)['status'],'matched')
