"""Conditional application selection never grants authority from configuration."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from agent_harness_lab import config
from agent_harness_lab import conditional_application as application_protocol
from agent_harness_lab.evaluation.regression_review import ReviewStore
from agent_harness_lab.evaluation.bank_qualification import source_roots

POLICY='controller-agents-conditional-apply-v1'


def application():
    return dict(policy=POLICY,review_store='/synthetic/private-review',authorization_id='a'*64,bank_case_id='b'*64,
        delegation_request='/synthetic/private-review/delegations/c/request.json',
        delegation_signature='/synthetic/private-review/delegations/c/request.json.sig',
        method=dict(experiment_id='synthetic-predeclared',repeats=1,seed=7,max_trials=6,min_roots=2,
            effect_margin=.01,family_margin=.01,alpha=.99,family_weights=dict(synthetic=1.),precision_half_width=None))


def settings():
    return dict(schema_version=2,profile='cooperative',recommendation_only=False,harness_paths=['AGENTS.md'],
        runtime=dict(name='responses',inference_settings='/synthetic/settings.json',cgroup_root='/synthetic/delegation',
            image_root='/synthetic/image',image_sha256='c'*64,disk_bytes=67108864,permission_audit='permission-results-v1'),
        limits=dict(seconds=150,trials=24),application=application())


class ConditionalConfigurationTests(unittest.TestCase):
    def test_frozen_oracle_pins_actual_native_interpreter_before_spec_construction(self):
        from agent_harness_lab.evaluation.bank_tools import export_manifest
        from agent_harness_lab.evaluation.oracle import identity
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=export_manifest(config.starter(),root/'bank',max_trials=4,task_ids=['regression','tool-recovery'])
            for version in ('baseline','candidate'):
                (root/version).mkdir();(root/version/'AGENTS.md').write_text('Synthetic '+version)
            families={task['family'] for task in source['tasks']}
            declared=dict(application()['method'],max_trials=4,family_weights={name:1/len(families) for name in families})
            # An exported host interpreter is a real input to the oracle identity.
            for task in source['tasks']:task['grader'][0]='/synthetic/source/python'
            before=identity(root/'bank',source['tasks'],source['grader_bundles'])
            spec=application_protocol._bind_spec(declared,source,root/'bank',root/'baseline',root/'candidate','d'*64)
            self.assertNotEqual(spec.grader_hash,before)
            self.assertTrue(all(task['grader'][0]=='/usr/bin/python3' for task in source['tasks']))
            self.assertEqual(spec.grader_hash,identity(root/'bank',source['tasks'],source['grader_bundles']))

    def test_additive_schema_two_accepts_only_the_explicit_bounded_policy(self):
        value=config.prepare(settings())
        self.assertEqual(value['schema_version'],2);self.assertEqual(value['application'],application())
        self.assertFalse(value['recommendation_only'])

    def test_legacy_defaults_and_recommendation_only_contract_are_unchanged(self):
        value=config.defaults();self.assertEqual(value['schema_version'],1);self.assertTrue(value['recommendation_only'])
        self.assertNotIn('application',value)
        with self.assertRaises(ValueError):config.prepare(dict(recommendation_only=False))

    def test_mixed_surfaces_missing_audit_and_incomplete_predeclared_method_are_rejected(self):
        for name,change in (('scope',lambda v:v.update(harness_paths=['AGENTS.md','CLAUDE.md'])),
                ('runtime',lambda v:v['runtime'].pop('permission_audit')),
                ('policy',lambda v:v['application'].update(policy='unqualified')),
                ('method',lambda v:v['application']['method'].pop('alpha')),
                ('locator',lambda v:v['application'].update(authorization_id='future-signature'))):
            with self.subTest(name=name):
                value=settings();change(value)
                with self.assertRaises(ValueError):config.prepare(value)

    def test_schema_one_cannot_smuggle_application_authority(self):
        value=settings();value['schema_version']=1;value['recommendation_only']=True
        with self.assertRaises(ValueError):config.prepare(value)


class AuthorizationLocatorTests(unittest.TestCase):
    def test_baseline_body_is_not_duplicated_into_authorization_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);project=root/'project';project.mkdir();(project/'AGENTS.md').write_text('SYNTHETIC_BODY\n'*8192)
            store=ReviewStore(root/'review');created=application_protocol.allocate(store,project)
            case,inputs,_=store.opened(created['authorization_id'])
            self.assertEqual(set(inputs['baseline']),{'sha256','mode'})
            self.assertNotIn('SYNTHETIC_BODY',(case/'inputs.json').read_text())
            self.assertLess((case/'inputs.json').stat().st_size,4096)

    def test_private_application_profiles_cannot_be_relabelled_for_public_export(self):
        from agent_harness_lab.evaluation.benchmark import public_export_allowed
        for profile in (application_protocol.POLICY,application_protocol.AUTH_PROFILE):
            value=dict(profile=profile,publication_allowed=True,provenance=dict(source_kind='synthetic',approved_publication=True,
                source_ref='synthetic:application',derived_from_private=False))
            self.assertFalse(public_export_allowed([value]))

    def test_labels_and_unregistered_handles_do_not_mint_a_live_application_session(self):
        for value in ({'authorized':True},application_protocol._Session(),None):
            with self.subTest(kind=type(value).__name__):
                with self.assertRaises(ValueError):application_protocol._owned(value)

    def test_locator_uses_existing_private_journal_and_preserves_project_source_protection(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);project=root/'project';project.mkdir();(project/'AGENTS.md').write_text('Synthetic baseline\n')
            store=ReviewStore(root/'review');before=(project/'AGENTS.md').read_bytes()
            created=application_protocol.allocate(store,project)
            shown=store.show(created['authorization_id'])
            self.assertEqual(shown['state'],'application_authorization')
            self.assertEqual(shown['authorization'],'not_authorized')
            self.assertFalse(shown['automatic_promotion'])
            with store.locked():self.assertEqual(source_roots(store),[project])
            self.assertEqual((project/'AGENTS.md').read_bytes(),before)
            with self.assertRaises(ValueError):store.draft(created['authorization_id'])

    def test_missing_executable_or_overlapping_target_cannot_allocate(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);project=root/'project';project.mkdir();store=ReviewStore(root/'review')
            with self.assertRaises(ValueError):application_protocol.allocate(store,project)
            target=project/'AGENTS.md';target.write_text('Synthetic baseline\n');target.chmod(0o755)
            with self.assertRaises(ValueError):application_protocol.allocate(store,project)
            target.chmod(0o644)
            target.write_bytes(b'\xff')
            with self.assertRaises(ValueError):application_protocol.allocate(store,project)
            target.write_text('Synthetic baseline\n')
            with self.assertRaises(ValueError):application_protocol.allocate(ReviewStore(project/'review'),project)

    def test_incomplete_final_diagnostics_never_record_provider_exception_text(self):
        from types import SimpleNamespace
        from agent_harness_lab.evaluation.oracle_history import _conditional_failure
        from agent_harness_lab.promotion.prerequisites import encode_receipt
        scope=SimpleNamespace(lost=True,closed=False,data=dict(events=[dict(complete=True)],instruction_requests=[],pairs=[]))
        scope._state=lambda:encode_receipt(scope.data)
        comparison=SimpleNamespace(active=None,lost=True,closed=True,rows=[],expected=[1,2],pending=[])
        diagnostic=_conditional_failure(ValueError('SYNTHETIC_PRIVATE_PROVIDER_BODY'),'trial_evaluation',scope,comparison)
        self.assertNotIn('SYNTHETIC_PRIVATE_PROVIDER_BODY',json.dumps(diagnostic))
        self.assertEqual(diagnostic['reason'],'unclassified');self.assertEqual(diagnostic['comparison']['expected'],2)
        known=_conditional_failure(ValueError('fresh comparison is incomplete'),'comparison_finish',scope,comparison)
        self.assertEqual(known['reason'],'comparison_incomplete')

    def test_changed_locator_input_cannot_become_an_authorization_case(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);project=root/'project';project.mkdir();(project/'AGENTS.md').write_text('Synthetic baseline\n')
            store=ReviewStore(root/'review');created=application_protocol.allocate(store,project)
            path=store.case(created['authorization_id'])/'inputs.json';value=json.loads(path.read_text())
            value['project']=str(root/'different');path.write_text(json.dumps(value))
            with self.assertRaises(ValueError):store.show(created['authorization_id'])


if __name__=='__main__':unittest.main()
