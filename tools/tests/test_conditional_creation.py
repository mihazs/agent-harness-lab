"""Absent enrollment and real Journal creation do not manufacture application grants."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from agent_harness_lab import conditional_application as application,config
from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.closed_inputs import project_selection,project_snapshot
from agent_harness_lab.evaluation.regression_review import ReviewStore
from agent_harness_lab.harness_change_plan import HarnessChangePlan,REFERENCE_NAMES
from agent_harness_lab.private_journal import encoded,record
from agent_harness_lab.promotion.journal import Journal,file_record
from agent_harness_lab.workflow import candidate_states
import test_conditional_application_authority as authority_fixture

CREATE_POLICY='controller-agents-create-conditional-apply-v1'
CREATE_AUTH_PROFILE='controller-instruction-creation-authorization-v1'


def plan(before,after):
    return HarnessChangePlan.create(before,after,references={name:None for name in REFERENCE_NAMES},
        diagnosis=[],falsifiers=[],dependencies=[],controls={},deferred=())


class CreationPolicyTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.root=Path(temporary.name)
        self.project=self.root/'project';self.project.mkdir();self.target=self.project/'AGENTS.md'
        self.store=ReviewStore(self.root/'review')

    def allocate(self,*extra):
        output=io.StringIO()
        with redirect_stdout(output):
            code=main(['application-review','--store',str(self.store.root),'allocate',str(self.project),*extra])
        self.assertEqual(code,0);return json.loads(output.getvalue())

    def test_public_creation_allocator_records_exact_absence_and_private_routing(self):
        expected=os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
        if expected:
            import agent_harness_lab
            self.assertTrue(Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve()))
        with self.assertRaises(SystemExit):self.allocate()
        locator=self.allocate('--operation','create');case,inputs,events=self.store.opened(locator['authorization_id'])
        self.assertEqual(inputs['profile'],CREATE_AUTH_PROFILE);self.assertEqual(inputs['baseline'],{'membership':'absent'})
        self.assertFalse(self.target.exists());self.assertEqual(self.store.show(locator['authorization_id'])['profile'],CREATE_AUTH_PROFILE)
        from agent_harness_lab.evaluation.bank_qualification import source_roots
        self.assertEqual(source_roots(self.store),[self.project])
        with self.assertRaisesRegex(ValueError,'not reproduced'):self.store.checked_evidence(case,inputs,events)
        review=self.root/'generic-review.json';review.write_bytes(encoded(dict(schema_version=1,case_id=locator['authorization_id'],
            expected_event_sha256=events[-1][1],evidence_sha256=None,decision='retired',evidence_refs=[],reason='Synthetic review',
            reviewer=dict(identity='synthetic-reviewer',source='operator_attestation',provenance='synthetic',basis_kind='synthetic',basis='Synthetic'))))
        with self.assertRaisesRegex(ValueError,'dedicated signed approval purpose'):self.store.review(locator['authorization_id'],review)
        self.assertEqual(self.store.opened(locator['authorization_id'])[2],events)
        from agent_harness_lab.evaluation.benchmark import public_export_allowed
        for profile in (CREATE_POLICY,CREATE_AUTH_PROFILE):
            self.assertFalse(public_export_allowed([dict(profile=profile,publication_allowed=True,provenance=dict(
                source_kind='synthetic',approved_publication=True,source_ref='synthetic:creation',derived_from_private=False))]))

    def test_existing_bank_exposure_classification_accepts_only_the_validated_creation_case(self):
        from agent_harness_lab.evaluation import bank_qualification as bankq
        import test_bank_qualification_final as bank_fixture
        self.allocate('--operation','create')
        bank,declaration=bank_fixture.proposal(self.root)
        intaken=bankq.intake(self.store,bank,declaration)
        state=dict(schema_version=2,profile='protected-diagnostic-access-v1',reservations={},observed_assets={})
        with self.store.locked():
            observed,assets=bankq.exposure_check(self.store,self.store.case(intaken['case_id']),state)
        self.assertEqual(len(observed),64);self.assertTrue(assets);self.assertFalse(self.target.exists())

    def test_creation_allocation_rejects_every_present_entry_including_empty_and_dangling(self):
        for kind in ('empty','directory','symlink','dangling'):
            with self.subTest(kind=kind):
                if kind=='empty':self.target.touch()
                elif kind=='directory':self.target.mkdir()
                else:self.target.symlink_to(self.project/('ahl.json' if kind=='symlink' else 'missing'))
                if kind=='symlink':(self.project/'ahl.json').write_text('{}')
                with self.assertRaises(SystemExit):self.allocate('--operation','create')
                if kind=='directory':self.target.rmdir()
                else:self.target.unlink()

    def test_default_allocator_preserves_the_existing_canonical_baseline_shape(self):
        self.target.write_text('Synthetic existing rule\n');self.target.chmod(0o640)
        with patch.object(application.secrets,'token_hex',return_value='a'*64):locator=self.allocate()
        observed=record(self.store.case(locator['authorization_id'])/'inputs.json')[0]
        current=file_record(self.target)
        expected=dict(schema_version=1,profile=application.AUTH_PROFILE,project=str(self.project),
            baseline={name:current[name] for name in ('sha256','mode')},nonce='a'*64)
        self.assertEqual(encoded(observed),encoded(expected));self.assertEqual(locator['authorization_id'],application.hashed(expected))

    def test_frozen_creation_plan_requires_empty_baseline_and_only_one_nonexec_utf8_candidate(self):
        baseline=self.root/'baseline';candidate=self.root/'candidate';baseline.mkdir();candidate.mkdir()
        target=candidate/'AGENTS.md';target.write_text('Synthetic new rule\n');target.chmod(0o640)
        after=candidate_states(candidate,{})
        creation=plan({},after);application.validate_plan(creation,CREATE_POLICY,baseline,candidate)
        for policy in (application.POLICY,application.REMOVE_POLICY):
            with self.assertRaises(ValueError):application.validate_plan(creation,policy,baseline,candidate)
        for mutation in ('baseline','executable','invalid_utf8','extra_file','extra_directory'):
            with self.subTest(mutation=mutation):
                extra=None
                if mutation=='baseline':extra=baseline/'AGENTS.md';extra.write_text('present')
                elif mutation=='executable':target.chmod(0o750)
                elif mutation=='invalid_utf8':target.write_bytes(b'\xff')
                elif mutation=='extra_file':extra=candidate/'CLAUDE.md';extra.write_text('extra')
                else:extra=candidate/'empty-directory';extra.mkdir()
                with self.assertRaises((ValueError,UnicodeError)):
                    application.validate_plan(plan(candidate_states(baseline,{}),candidate_states(candidate,{})),CREATE_POLICY,baseline,candidate)
                if extra is not None:
                    if extra.is_dir():extra.rmdir()
                    else:extra.unlink()
                target.write_text('Synthetic new rule\n');target.chmod(0o640)


@unittest.skipUnless(Path('/usr/bin/ssh-keygen').is_file(),'optional OpenSSH verifier unavailable')
class CreationSignedIntentTests(unittest.TestCase):
    def fixture(self):
        value=authority_fixture.ConditionalApplicationAuthorityTests();value.setUp();self.addCleanup(value.doCleanups)
        return value

    def configure(self,value,authorization,policy=CREATE_POLICY):
        path=value.project/'ahl.json';document=json.loads(path.read_bytes())
        document['application'].update(policy=policy,authorization_id=authorization)
        path.write_bytes(encoded(config.prepare(document)))
        declared,pin=config.load(path);return declared,pin,config.execution_options(value.project,declared,{},set())

    def prepare_creation(self,value):
        (value.project/'AGENTS.md').unlink()
        value.authorization=value.invoke('allocate',str(value.project),'--operation','create')['authorization_id']
        declared,pin,options=self.configure(value,value.authorization)
        prepared=application.prepare(value.store,value.authorization,value.project,declared,pin,options,'synthetic-reviewer')
        value.request=Path(prepared['request_path'])

    def test_public_fresh_creation_signature_binds_absence_and_never_issues_a_grant(self):
        value=self.fixture();self.prepare_creation(value)
        request=record(value.request)[0]['review']
        self.assertEqual(request['profile'],CREATE_AUTH_PROFILE)
        self.assertEqual(request['binding']['baseline'],{'membership':'absent'})
        self.assertEqual(request['binding']['allowed_operations'],[dict(path='AGENTS.md',action='create',facets=['instruction'])])
        result=value.invoke('authorize',value.authorization,str(value.request),str(value.sign()))
        self.assertEqual(result['state'],'application_authorized');self.assertFalse((value.project/'AGENTS.md').exists())
        with self.assertRaises(ValueError):application._owned(result)

    def test_existing_authorization_cannot_prepare_creation(self):
        value=self.fixture()
        value.authorization=value.invoke('allocate',str(value.project))['authorization_id']
        (value.project/'AGENTS.md').unlink()
        declared,pin,options=self.configure(value,value.authorization)
        with self.assertRaisesRegex(ValueError,'baseline changed'):application.prepare(value.store,value.authorization,value.project,declared,pin,options,'synthetic-reviewer')

    def test_update_and_removal_signatures_cannot_be_reinterpreted_as_creation(self):
        for policy in (application.POLICY,application.REMOVE_POLICY):
            with self.subTest(policy=policy):
                value=self.fixture()
                if policy==application.REMOVE_POLICY:
                    value.authorization=value.invoke('allocate',str(value.project))['authorization_id']
                    declared,pin,options=self.configure(value,value.authorization,policy)
                    prepared=application.prepare(value.store,value.authorization,value.project,declared,pin,options,'synthetic-reviewer')
                    value.request=Path(prepared['request_path'])
                signature=value.sign();before=value.events()
                (value.project/'AGENTS.md').unlink();self.configure(value,value.authorization)
                with self.assertRaises(ValueError):application.authorize(value.store,value.authorization,value.request,signature,deadline=time.monotonic()+10)
                self.assertEqual(value.events(),before);self.assertFalse((value.project/'AGENTS.md').exists())

    def test_creation_signature_cannot_be_reinterpreted_as_update_or_remove(self):
        for policy in (application.POLICY,application.REMOVE_POLICY):
            with self.subTest(policy=policy):
                value=self.fixture();self.prepare_creation(value);signature=value.sign();before=value.events()
                (value.project/'AGENTS.md').write_text('Synthetic foreign present rule\n')
                self.configure(value,value.authorization,policy)
                with self.assertRaises(ValueError):application.authorize(value.store,value.authorization,value.request,signature,deadline=time.monotonic()+10)
                self.assertEqual(value.events(),before);self.assertEqual((value.project/'AGENTS.md').read_text(),'Synthetic foreign present rule\n')


class CreationJournalTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.root=Path(temporary.name)
        self.project=self.root/'project';self.project.mkdir();self.target=self.project/'AGENTS.md'
        (self.project/'ahl.json').write_text('{}\n')
        self.before=project_snapshot(self.project,project_selection(self.project),time.monotonic()+5)
        candidate=self.root/'candidate';candidate.mkdir();new=candidate/'AGENTS.md';new.write_text('Synthetic new rule\n');new.chmod(0o640)
        self.after_record=file_record(new)
        self.after=application._after_project(self.before,self.after_record)
        self.assertEqual([row[0] for row in self.after.files],['AGENTS.md','ahl.json'])
        self.journal=Journal(self.project,self.root/'journal')
        self.prepared=self.journal.prepare({'AGENTS.md':None},{'AGENTS.md':self.after_record},dict(source=CREATE_POLICY))
        self.permitted=dict(journal=self.journal,record=self.prepared,after=self.after_record,checks=0,before_project=self.before,after_project=self.after)

    def guard(self):
        expected=application.mutation_checkpoint(self.permitted,self.project,CREATE_POLICY)
        observed=project_snapshot(self.project,project_selection(self.project),time.monotonic()+5)
        self.assertEqual(observed,expected);self.permitted['checks']+=1

    def commit(self):
        self.journal._execute(self.prepared['deployment'],pre_write=self.guard)
        self.assertEqual(self.target.read_bytes(),b'Synthetic new rule\n');self.assertEqual(self.target.stat().st_mode&0o777,0o640)
        self.assertEqual(self.permitted['checks'],5)

    def test_real_creation_exact_five_checkpoints_public_rollback_restores_absence(self):
        self.commit();self.assertEqual(application._journal_outcome(self.journal,self.prepared,self.after)['status'],'applied')
        with patch('agent_harness_lab.workflow.state_root',return_value=self.root/'state'),patch('agent_harness_lab.workflow.identity',return_value='synthetic'):
            # The public command resolves the same real Journal, without a new writer.
            with patch('agent_harness_lab.promotion.journal.Journal',return_value=self.journal),redirect_stdout(io.StringIO()):
                self.assertEqual(main(['rollback',self.prepared['deployment'],'--project',str(self.project)]),0)
        self.assertFalse(self.target.exists());self.assertEqual(project_snapshot(self.project,project_selection(self.project),time.monotonic()+5),self.before)

    def test_interrupt_after_creation_restores_absence(self):
        def interrupt():
            self.guard()
            if self.permitted['checks']==4:raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):self.journal._execute(self.prepared['deployment'],pre_write=interrupt)
        self.assertFalse(self.target.exists());self.assertIsNone(self.journal.active)
        self.assertEqual(self.journal.load(self.prepared['deployment'])['restoration'],'restored')

    def test_foreign_appearance_before_create_is_preserved(self):
        self.target.write_text('Synthetic foreign file\n')
        with self.assertRaises(ValueError):self.journal._execute(self.prepared['deployment'],pre_write=self.guard)
        self.assertEqual(self.target.read_text(),'Synthetic foreign file\n');self.assertIsNone(self.journal.active)

    def test_modified_actual_staging_mode_rejects_creation_and_preserves_absence(self):
        def modified():
            if self.permitted['checks']==2:self.journal._pending_snapshot[0].chmod(0o600)
            self.guard()
        with self.assertRaisesRegex(ValueError,'staging snapshot changed'):
            self.journal._execute(self.prepared['deployment'],pre_write=modified)
        self.assertFalse(self.target.exists());self.assertIsNone(self.journal.active)

    def test_committed_creation_readback_replay_and_foreign_mode_conflict(self):
        self.commit();observed=application._journal_outcome(self.journal,self.prepared,self.after)
        self.assertEqual((observed['status'],observed['changed_files']),('applied',['AGENTS.md']))
        with self.assertRaisesRegex(ValueError,'replayed'):application.mutation_checkpoint(self.permitted,self.project,CREATE_POLICY)
        self.target.chmod(0o600)
        self.assertEqual(application._journal_outcome(self.journal,self.prepared,self.after)['status'],'requires_intervention')
        with self.assertRaisesRegex(ValueError,'rollback conflict'):self.journal.rollback(self.prepared['deployment'])
        self.assertEqual(self.target.stat().st_mode&0o777,0o600)


if __name__=='__main__':unittest.main()
