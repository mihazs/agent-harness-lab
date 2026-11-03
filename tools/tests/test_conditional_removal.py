"""Signed removal intent and physical Journal checks do not issue application grants."""
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from agent_harness_lab import conditional_application as application,config
from agent_harness_lab.evaluation.closed_inputs import project_selection,project_snapshot
from agent_harness_lab.harness_change_plan import HarnessChangePlan,REFERENCE_NAMES
from agent_harness_lab.private_journal import encoded,record
from agent_harness_lab.promotion.journal import Journal,file_record
from test_conditional_application import settings
import test_conditional_application_authority as authority_fixture

REMOVE_POLICY='controller-agents-remove-conditional-apply-v1'


def declared_plan(before,after):
    return HarnessChangePlan.create(before,after,references={name:None for name in REFERENCE_NAMES},
        diagnosis=[],falsifiers=[],dependencies=[],controls={},deferred=())


class RemovalPolicyTests(unittest.TestCase):
    def test_explicit_removal_policy_is_a_distinct_configuration(self):
        declared=settings();declared['application']['policy']=REMOVE_POLICY
        accepted=config.prepare(declared)
        self.assertEqual(accepted['application']['policy'],REMOVE_POLICY)
        self.assertFalse(accepted['recommendation_only'])
        self.assertEqual(application.action(application.POLICY),'update')
        self.assertEqual(application.action(REMOVE_POLICY),'remove')
        for policy in ('controller-agents-create-conditional-apply-v1',None,[]):
            with self.subTest(policy=policy),self.assertRaises(ValueError):application.action(policy)

    def test_removal_profile_stays_private_even_with_a_public_label(self):
        from agent_harness_lab.evaluation.benchmark import public_export_allowed
        self.assertFalse(public_export_allowed([dict(profile=REMOVE_POLICY,publication_allowed=True,
            provenance=dict(source_kind='synthetic',approved_publication=True,source_ref='synthetic:removal',derived_from_private=False))]))

    def test_labels_cannot_supply_a_signed_action_hint_or_mutation_grant(self):
        self.assertEqual(application.proposer_hint(None),'')
        for value in ({'policy':REMOVE_POLICY},application._Session(),None):
            if value is not None:
                with self.assertRaises(ValueError):application.proposer_hint(value)
            with self.assertRaises(ValueError):application._bank_guard(value,None)

    def test_frozen_plan_accepts_only_existing_instruction_removal_under_remove_policy(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);baseline=root/'baseline';candidate=root/'candidate';baseline.mkdir();candidate.mkdir()
            target=baseline/'AGENTS.md';target.write_text('Synthetic existing rule\n');target.chmod(0o640)
            from agent_harness_lab.workflow import candidate_states
            before=candidate_states(baseline,{})
            removal=declared_plan(before,{})
            application.validate_plan(removal,REMOVE_POLICY,baseline,candidate)
            with self.assertRaises(ValueError):application.validate_plan(removal,application.POLICY,baseline,candidate)
            (candidate/'AGENTS.md').write_text('changed\n');after=candidate_states(candidate,before)
            with self.assertRaises(ValueError):application.validate_plan(removal,REMOVE_POLICY,baseline,candidate)
            update=declared_plan(before,after)
            with self.assertRaises(ValueError):application.validate_plan(update,REMOVE_POLICY,baseline,candidate)
            with self.assertRaises(ValueError):application.validate_plan(declared_plan({},after),REMOVE_POLICY,baseline,candidate)
            (candidate/'CLAUDE.md').write_text('extra\n')
            with self.assertRaises(ValueError):application.validate_plan(declared_plan(before,candidate_states(candidate,before)),REMOVE_POLICY,baseline,candidate)
            for item in candidate.iterdir():item.unlink()
            (candidate/'empty-directory').mkdir()
            with self.assertRaises(ValueError):application.validate_plan(removal,REMOVE_POLICY,baseline,candidate)
            (candidate/'empty-directory').rmdir();target.chmod(0o750)
            with self.assertRaises(ValueError):application.validate_plan(removal,REMOVE_POLICY,baseline,candidate)


@unittest.skipUnless(Path('/usr/bin/ssh-keygen').is_file(),'optional OpenSSH verifier unavailable')
class RemovalSignedIntentTests(unittest.TestCase):
    def fixture(self):
        value=authority_fixture.ConditionalApplicationAuthorityTests();value.setUp();self.addCleanup(value.doCleanups)
        return value

    def change_policy(self,value,policy):
        path=value.project/'ahl.json';document=json.loads(path.read_bytes());document['application']['policy']=policy
        path.write_bytes(encoded(config.prepare(document)))

    def test_update_signature_cannot_be_reinterpreted_as_removal(self):
        value=self.fixture();request,_=record(value.request);saved=deepcopy(request)
        self.assertEqual(request['review']['binding']['policy'],application.POLICY)
        self.assertEqual(request['review']['binding']['allowed_operations'],[dict(path='AGENTS.md',action='update',facets=['instruction'])])
        signature=value.sign();self.change_policy(value,REMOVE_POLICY)
        value.rejected(signature,'configuration or references changed')
        self.assertEqual(record(value.request)[0],saved)

    def prepare_removal(self,value):
        authorization=application.allocate(value.store,value.project)['authorization_id']
        path=value.project/'ahl.json';document=json.loads(path.read_bytes())
        document['application'].update(policy=REMOVE_POLICY,authorization_id=authorization)
        path.write_bytes(encoded(config.prepare(document)))
        declared,pin=config.load(path);options=config.execution_options(value.project,declared,{},set())
        prepared=application.prepare(value.store,authorization,value.project,declared,pin,options,'synthetic-reviewer')
        value.authorization=authorization;value.request=Path(prepared['request_path'])

    def test_fresh_removal_signature_binds_only_remove_and_cannot_be_reinterpreted_as_update(self):
        value=self.fixture();self.prepare_removal(value)
        request,_=record(value.request)
        self.assertEqual(request['review']['binding']['policy'],REMOVE_POLICY)
        self.assertEqual(request['review']['binding']['allowed_operations'],[dict(path='AGENTS.md',action='remove',facets=['instruction'])])
        signature=value.sign();self.change_policy(value,application.POLICY)
        value.rejected(signature,'configuration or references changed')

    def test_running_removal_intent_observes_public_retirement_without_minting_authority(self):
        value=self.fixture();self.prepare_removal(value)
        application.authorize(value.store,value.authorization,value.request,value.sign(),deadline=time.monotonic()+10)
        binding=record(value.request)[0]['review']['binding']
        with value.store.locked():
            case,_,events=application.opened(value.store,value.authorization)
            value.store.append(case,events,'application_started','application_running',dict(binding_sha256=application.hashed(binding)))
        data=dict(store=value.store,authorization_id=value.authorization,binding=binding,running_head=events[-1][1])
        application._running_authorization(data)
        value.invoke('retire',value.authorization)
        with self.assertRaisesRegex(ValueError,'changed or was retired'):application._running_authorization(data)
        self.assertEqual((value.project/'AGENTS.md').read_text(),'Synthetic baseline\n')

    def test_fresh_signed_removal_records_intent_without_issuing_a_live_grant(self):
        value=self.fixture();self.prepare_removal(value)
        observed=application.authorize(value.store,value.authorization,value.request,value.sign(),deadline=time.monotonic()+10)
        self.assertEqual(observed['state'],'application_authorized')
        with self.assertRaises(ValueError):application._owned(observed)
        self.assertEqual((value.project/'AGENTS.md').read_text(),'Synthetic baseline\n')


class RemovalJournalTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.root=Path(temporary.name)
        self.enterContext(patch.dict(os.environ,{'XDG_STATE_HOME':str(self.root/'state')}))
        self.project=self.root/'project';self.project.mkdir();self.target=self.project/'AGENTS.md'
        self.target.write_text('Synthetic existing rule\n');self.target.chmod(0o640);(self.project/'ahl.json').write_text('{}\n')
        self.before=project_snapshot(self.project,project_selection(self.project),time.monotonic()+5)
        self.after=replace(self.before,files=tuple(row for row in self.before.files if row[0]!='AGENTS.md'))
        self.journal=Journal(self.project,self.root/'journal')
        self.prepared=self.journal.prepare({'AGENTS.md':file_record(self.target)},{'AGENTS.md':None},dict(source=REMOVE_POLICY))
        self.permitted=dict(journal=self.journal,record=self.prepared,after=None,checks=0,before_project=self.before,after_project=self.after)

    def guard(self):
        expected=application.mutation_checkpoint(self.permitted,self.project,REMOVE_POLICY)
        self.assertEqual(project_snapshot(self.project,project_selection(self.project),time.monotonic()+5),expected)
        self.permitted['checks']+=1

    def commit(self):
        self.journal._execute(self.prepared['deployment'],pre_write=self.guard)
        self.assertEqual(self.permitted['checks'],4);self.assertFalse(self.target.exists())

    def test_real_removal_observes_exact_four_checkpoints_and_rollback_restores_mode(self):
        self.commit();self.assertEqual(application.guard_count(application.POLICY),5)
        self.assertEqual(application._journal_outcome(self.journal,self.prepared,self.after)['status'],'applied')
        self.journal.rollback(self.prepared['deployment'])
        self.assertEqual((self.target.read_bytes(),self.target.stat().st_mode&0o777),(b'Synthetic existing rule\n',0o640))

    def test_update_policy_cannot_consume_a_removal_record(self):
        with self.assertRaisesRegex(ValueError,'operation differs'):
            self.journal._execute(self.prepared['deployment'],pre_write=lambda:application.mutation_checkpoint(self.permitted,self.project,application.POLICY))
        self.assertEqual(project_snapshot(self.project,project_selection(self.project),time.monotonic()+5),self.before)
        self.assertIsNone(self.journal.active)

    def test_interrupt_after_unlink_restores_actual_owned_membership_bytes_and_mode(self):
        def interrupt():
            self.guard()
            if self.permitted['checks']==3:
                self.assertFalse(self.target.exists());raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):self.journal._execute(self.prepared['deployment'],pre_write=interrupt)
        self.assertEqual(project_snapshot(self.project,project_selection(self.project),time.monotonic()+5),self.before)
        receipt=self.journal.load(self.prepared['deployment'])
        self.assertEqual((receipt['status'],receipt['restoration'],receipt['remaining_changes']),('conflict','restored',[]))
        self.assertIsNone(self.journal.active)

    def test_committed_absence_is_truthful_after_metadata_error_and_guard_replay_is_rejected(self):
        with patch('agent_harness_lab.evaluation.responses.request',side_effect=AssertionError('observer cannot infer')):
            self.commit()
            with self.assertRaises(OSError):(self.root/'missing'/'workflow.json').write_text('{}')
            observed=application._journal_outcome(self.journal,self.prepared,self.after)
        self.assertEqual((observed['status'],observed['changed_files'],observed['target_readback']),('applied',['AGENTS.md'],'matched'))
        with self.assertRaisesRegex(ValueError,'replayed'):application.mutation_checkpoint(self.permitted,self.project,REMOVE_POLICY)
        self.assertFalse(self.target.exists())

    def test_foreign_recreation_requires_intervention_and_blocks_rollback(self):
        self.commit();self.target.write_text('Synthetic foreign recreation\n');self.target.chmod(0o600)
        observed=application._journal_outcome(self.journal,self.prepared,self.after)
        self.assertEqual((observed['status'],observed['remaining_change_ownership']),('requires_intervention','unverified'))
        with self.assertRaisesRegex(ValueError,'rollback conflict'):self.journal.rollback(self.prepared['deployment'])
        self.assertEqual((self.target.read_bytes(),self.target.stat().st_mode&0o777),(b'Synthetic foreign recreation\n',0o600))


if __name__=='__main__':unittest.main()
