"""Opt-in cohort identities preserve the frozen legacy plan and schedule."""
from dataclasses import replace
import json
import unittest

from agent_harness_lab.evaluation.plan import ExperimentSpec,schedule
from agent_harness_lab.evaluation.statistics import TrialRecord,analyze
from agent_harness_lab.promotion.policy import decide


class PreservationPlanTests(unittest.TestCase):
    def spec(self):
        return ExperimentSpec('synthetic-preservation-plan',(('one','root-one','family'),('two','root-two','family')),
            2,17,8,2,.05,.1,.05,(('family',1.0),),'1'*64,'2'*64,'3'*64,'4'*64)

    def test_original_identity_and_every_trial_id_are_byte_compatible(self):
        spec=self.spec()
        self.assertNotIn('cohort_manifest_sha256',spec.to_dict())
        self.assertEqual(spec.digest,'851df1a634b308fbe27515a4ddd991155a3a5c6d37c689a8ac1a45ef46439069')
        self.assertEqual([row['trial_id'] for row in schedule(spec)],[
            '69511d8c2f29a26fa40dee947ae70ab9f8839656865ce5c99810a66721484337',
            '34e21a7e4edeb0dd19a50342dfc6580e9e3dcdd3d91c819e12fb55e16a78b5e8',
            '243e1a4435945e5a2550327e65a869da7c1ab047955378fd945fb89dc2d8cd4a',
            '425eb6406bc16377e913f189cabc65322e16486f578e61ef5405651cb556bcd8',
            '0e068e98010bf22dfe3decb3d775751dbd4c2e02dbf27d260ff6ae774647a1a4',
            '48cf1ff10ce6661f0bc93a0a924989b9975c5de8bf40dd071c76c5df44639d4e',
            '82d3ba86096ceb5438d995b0b995dc14960b23d90853689d4faf502e3be2764e',
            '6c840e4bb5301acbff2793faa2584e376927dedbc7ef31732c6e9b9fc10b2108'])
        self.assertEqual(ExperimentSpec.parse(json.loads(json.dumps(spec.to_dict()))),spec)

    def test_changed_membership_pin_changes_plan_and_all_fresh_trial_ids(self):
        original=self.spec(); selected=replace(original,cohort_manifest_sha256='a'*64)
        self.assertNotEqual(original.digest,selected.digest)
        self.assertTrue({t['trial_id'] for t in schedule(original)}.isdisjoint(t['trial_id'] for t in schedule(selected)))
        self.assertEqual(ExperimentSpec.parse(json.loads(json.dumps(selected.to_dict()))),selected)
        self.assertNotEqual(selected.digest,replace(selected,cohort_manifest_sha256='b'*64).digest)

    def test_nullable_migration_and_strict_manifest_digest(self):
        spec=self.spec(); value=spec.to_dict()|dict(cohort_manifest_sha256=None)
        self.assertEqual(ExperimentSpec.parse(value).digest,spec.digest)
        for pin in (True,1,[],{},'', 'A'*64,'a'*63,'a'*65):
            with self.subTest(pin=pin),self.assertRaises(ValueError):
                replace(spec,cohort_manifest_sha256=pin).validate()

    def test_qualification_pin_alone_cannot_receive_a_favorable_preservation_decision(self):
        spec=replace(self.spec(),cohort_manifest_sha256='a'*64)
        rows=[TrialRecord(t['trial_id'],True,'graded','executable_grader',
            spec.baseline_hash,spec.candidate_hash,spec.grader_hash,spec.runtime_hash) for t in schedule(spec)]
        unresolved=analyze(spec,rows)
        self.assertEqual((unresolved['status'],unresolved['reason']),('blocked','preservation_binding_missing_or_invalid'))
        self.assertFalse(unresolved['publication_allowed'])
        final=replace(spec,profile='certification',partition='certification')
        decision=decide(final,dict(status='supported',oracle_validity='current'),isolation_verified=True,
            effective_runtime_verified=True,graders_audited=True,lineage_reviewed=True,certification_reserved=True)
        self.assertEqual((decision.status,decision.reason),('blocked','preservation_analysis_unavailable'))
        rejected=analyze(spec,[replace(row,critical_regression=True) for row in rows])
        self.assertEqual((rejected['status'],rejected['reason']),('rejected','critical_regression'))
