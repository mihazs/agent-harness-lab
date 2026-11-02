"""Independent synthetic annotation controls; no model, private traces or gold claims."""
from copy import deepcopy
import hashlib
import multiprocessing
from pathlib import Path
import tempfile
import unittest

from agent_harness_lab.diagnostics.corpus import CorpusStore, incident
from agent_harness_lab.private_journal import encoded, record


def sample(name='synthetic-incident',partition='development'):
    content='A synthetic artifact check failed; the terminal record is available.'
    return dict(schema_version=1,incident_id=name,root_id=name+'-root',family='synthetic-family',
        project_id='synthetic-project',time_group='synthetic-window',partition=partition,source_kind='synthetic',
        source_ref='synthetic:offline-fixture',source_revision_sha256=hashlib.sha256(name.encode()).hexdigest(),
        permission='approved',license='CC0-1.0',summary=content,predicted_summary='SUGGESTED_LABEL_MUST_STAY_HIDDEN',
        evidence=[dict(id='terminal',content=content,sha256=hashlib.sha256(content.encode()).hexdigest())],
        capture_complete=True,ambiguity=False,selection_reason='manual',rubric_sha256='a'*64,
        author='synthetic-author',supersedes=None)


def label(outcome='task_failure',recovery='unrecovered'):
    return dict(outcome=outcome,recovery=recovery,symptoms=['synthetic-symptom'],mechanisms=[],
        verification='reviewed_observation',evidence_refs=['terminal'],reason='Synthetic declared observation review')


def compete_claim(root,case,head,reviewer,results):
    try:
        CorpusStore(root).update(case,head,'claimed',reviewer=reviewer)
        results.put('claimed')
    except (ValueError,BlockingIOError): results.put('conflict')


class DiagnosticCorpusTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'corpus'; self.store=CorpusStore(self.root)

    def enqueue(self,value=None):
        return self.store.enqueue(value or sample())

    def update(self,item,kind,reviewer=None,value=None,reason=None):
        return self.store.update(item['case_id'],item['event_sha256'],kind,reviewer=reviewer,label=value,reason=reason)

    def reviewed(self,value=None,first=None,second=None,final=None):
        item=self.enqueue(value)
        for reviewer,annotation in (('synthetic-reviewer-one',first or label()),('synthetic-reviewer-two',second or label())):
            item=self.update(item,'claimed',reviewer)
            item=self.update(item,'submitted',reviewer,annotation)
        return self.update(item,'adjudicated','synthetic-adjudicator',final or first or label(),'Synthetic adjudication basis')

    def export(self):
        return self.store.export_development(self.store.snapshot()['revision_sha256'])

    def test_review_queue_preserves_blinding_submissions_and_declared_authority(self):
        item=self.enqueue(); case=item['case_id']
        self.assertEqual(item['state'],'pending')
        for reviewer in ('synthetic-reviewer-one','synthetic-reviewer-two'):
            item=self.update(item,'claimed',reviewer)
            packet=self.store.packet(case,reviewer)
            self.assertNotIn('SUGGESTED_LABEL_MUST_STAY_HIDDEN',str(packet))
            self.assertNotIn('label',packet['review_packet'])
            self.assertFalse(packet['prior_labels_visible'])
            item=self.update(item,'submitted',reviewer,label())
        self.assertEqual(item['state'],'reviewed'); self.assertEqual(item['disposition'],'agreement')
        self.assertEqual(self.export()['records'],[])
        item=self.update(item,'adjudicated','synthetic-adjudicator',label(),'Synthetic independent declared review')
        self.assertEqual(item['state'],'adjudicated')
        result=self.export(); row,=result['records']
        self.assertEqual(row['annotation']['outcome'],'task_failure')
        self.assertFalse(row['gold_label']); self.assertFalse(result['classifier_selection_allowed'])
        self.assertEqual(item['reviewer_independence'],'unverified')
        self.assertFalse(item['task_outcome_oracle']); self.assertFalse(result['publication_allowed'])
        self.assertEqual(self.root.stat().st_mode&0o777,0o700)

    def test_disagreements_and_original_labels_survive_adjudication(self):
        item=self.enqueue()
        for reviewer,annotation in (('one',label()),('two',label('successful','recovered'))):
            item=self.update(item,'claimed',reviewer); item=self.update(item,'submitted',reviewer,annotation)
        self.assertEqual(item['disposition'],'disagreement')
        item=self.update(item,'adjudicated','three',label('unknown','unknown'),'Contradictory synthetic evidence')
        self.assertEqual(item['disposition'],'unresolved'); self.assertEqual(self.export()['records'],[])
        events=self.store.history(self.store.case(item['case_id']))
        self.assertEqual([e['payload']['label']['outcome'] for e,_ in events if e['kind']=='submitted'],['task_failure','successful'])

    def test_self_review_wrong_claimant_and_stale_clients_cannot_overwrite(self):
        item=self.enqueue()
        with self.assertRaises(ValueError): self.update(item,'claimed','SYNTHETIC-AUTHOR')
        claimed=self.update(item,'claimed','one')
        with self.assertRaises(ValueError): self.update(item,'claimed','two')
        with self.assertRaises(ValueError): self.update(claimed,'submitted','two',label())
        submitted=self.update(claimed,'submitted','one',label())
        with self.assertRaises(ValueError): self.update(submitted,'claimed','ONE')
        claimed=self.update(submitted,'claimed','two'); reviewed=self.update(claimed,'submitted','two',label())
        with self.assertRaises(ValueError): self.update(reviewed,'adjudicated','one',label(),'Self adjudication')

    def test_concurrent_claims_have_one_winner(self):
        item=self.enqueue(); ctx=multiprocessing.get_context('fork'); results=ctx.Queue()
        children=[ctx.Process(target=compete_claim,args=(str(self.root),item['case_id'],item['event_sha256'],r,results)) for r in ('one','two')]
        for child in children: child.start()
        for child in children:
            child.join(5); self.assertFalse(child.is_alive()); self.assertEqual(child.exitcode,0)
        self.assertCountEqual([results.get(timeout=2) for _ in children],['claimed','conflict'])
        results.close(); results.join_thread()

    def test_incomplete_capture_missing_evidence_and_permission_cannot_be_gold_outcome(self):
        for change in (dict(capture_complete=False),dict(capture_complete=None),dict(permission='unverified'),dict(evidence=[])):
            value=sample(); value.update(change); value['incident_id']+=str(len(list(self.root.glob('cases/*'))))
            item=self.enqueue(value); item=self.update(item,'claimed','one')
            with self.assertRaises(ValueError): self.update(item,'submitted','one',label())
            unknown=label('unknown','unknown'); unknown.update(verification='unknown',evidence_refs=[],symptoms=[])
            item=self.update(item,'submitted','one',unknown)
            self.assertEqual(item['state'],'submitted')

    def test_outcome_distinctions_do_not_follow_keywords(self):
        for index,(outcome,recovery) in enumerate((('task_failure','unrecovered'),('successful','recovered'),
                ('external_failure','not_applicable'),('requirements_changed','not_applicable'),('successful','not_applicable'))):
            value=sample('synthetic-'+str(index)); value['summary']='Error failure timeout NOT a terminal outcome.'
            expected=label(outcome,recovery); self.reviewed(value,expected,expected,expected)
        rows=self.export()['records']
        self.assertCountEqual([(r['annotation']['outcome'],r['annotation']['recovery']) for r in rows],
            [('task_failure','unrecovered'),('successful','recovered'),('external_failure','not_applicable'),('requirements_changed','not_applicable'),('successful','not_applicable')])

    def test_new_roots_cannot_disguise_copied_protected_text(self):
        self.reviewed(sample('fitting','fitting'))
        self.enqueue(sample('heldout','validation'))
        with self.assertRaisesRegex(ValueError,'text aliases'): self.export()

    def test_root_or_incident_alias_across_partitions_is_rejected(self):
        for key in ('root_id','incident_id','source_revision_sha256'):
            with self.subTest(key=key),tempfile.TemporaryDirectory() as folder:
                store=CorpusStore(Path(folder)/'corpus')
                first=sample('first','fitting'); store.enqueue(first)
                second=sample('second','validation'); second[key]=first[key]; second['summary']='Distinct synthetic wording.'
                if key=='incident_id':
                    with self.assertRaisesRegex(ValueError,'explicit'): store.enqueue(second)
                    continue
                store.enqueue(second)
                with self.assertRaisesRegex(ValueError,'split leakage'):
                    store.export_development(store.snapshot()['revision_sha256'])

    def test_exposure_retires_protected_partition_to_development(self):
        value=sample('protected','certification'); item=self.reviewed(value)
        self.assertEqual(self.export()['records'],[])
        item=self.update(item,'exposed',reason='Used in synthetic development diagnosis')
        self.assertEqual(item['partition'],'development')
        row,=self.export()['records']; self.assertEqual(row['record']['partition'],'certification')
        self.assertEqual(self.store.snapshot()['items'][0]['partition'],'development')
        changed=deepcopy(value); changed.update(rubric_sha256='b'*64,supersedes=item['case_id'])
        requeued=self.enqueue(changed)
        self.assertEqual(requeued['partition'],'development')
        self.assertEqual(self.store.packet(requeued['case_id'],'fresh-reviewer')['partition'],'development')

    def test_source_or_rubric_requeue_has_fresh_review_and_immutable_history(self):
        old=self.reviewed(); changed=sample(); changed.update(rubric_sha256='b'*64,supersedes=old['case_id'])
        new=self.enqueue(changed)
        self.assertEqual(new['state'],'pending'); self.assertEqual(new['submissions'],0)
        self.assertEqual(self.export()['records'],[])
        self.assertEqual(len(self.store.snapshot()['items']),1)
        unchanged=sample(); unchanged['supersedes']=old['case_id']
        with self.assertRaisesRegex(ValueError,'changed source/rubric'): self.enqueue(unchanged)
        case=self.store.case(old['case_id']); original=(case/'incident.json').read_bytes()
        self.assertEqual(record(case/'incident.json')[0]['rubric_sha256'],'a'*64)
        (case/'incident.json').write_bytes(original+b' ')
        with self.assertRaises(ValueError): self.store.snapshot()

    def test_stale_export_and_reimport_never_reuse_reviews(self):
        self.reviewed(); result=self.export(); revision=result['revision_sha256']
        value=sample('new'); value['summary']='Fresh independently synthetic text.'; self.enqueue(value)
        with self.assertRaises(ValueError): self.store.export_development(revision)
        with tempfile.TemporaryDirectory() as folder:
            imported=CorpusStore(Path(folder)/'corpus'); item=imported.enqueue(result['records'][0]['record'])
            self.assertEqual(item['state'],'pending')
            self.assertEqual(imported.export_development(imported.snapshot()['revision_sha256'])['records'],[])

    def test_competing_requeues_and_unlinked_incident_revisions_are_rejected_atomically(self):
        old=self.enqueue(); first=sample(); first.update(rubric_sha256='b'*64,supersedes=old['case_id'])
        self.enqueue(first)
        second=sample(); second.update(rubric_sha256='c'*64,supersedes=old['case_id'])
        with self.assertRaisesRegex(ValueError,'competing'): self.enqueue(second)
        second['supersedes']=None
        with self.assertRaisesRegex(ValueError,'explicit'): self.enqueue(second)
        self.assertEqual(len(self.store.snapshot()['items']),1)

    def test_requeues_cannot_relabel_partition_lineage_or_source_privacy(self):
        for key,new_value in (('partition','validation'),('root_id','renamed-root'),('family','renamed-family'),
                ('project_id','renamed-project'),('time_group','renamed-window'),('source_kind','public')):
            store=CorpusStore(Path(self.temp.name)/('variant-'+key))
            old=store.enqueue(sample(partition='fitting'))
            changed=sample(partition='fitting')
            changed.update(rubric_sha256='b'*64,supersedes=old['case_id']); changed[key]=new_value
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'lineage/privacy/partition'):
                store.enqueue(changed)
            self.assertEqual(len(store.snapshot()['items']),1)

    def test_schema_bounds_duplicate_ids_changed_fragments_and_unsafe_store(self):
        for change in (dict(schema_version=True),dict(extra='field'),dict(ambiguity=1),dict(summary='x'*4097),
                dict(evidence=[dict(id='x',content='changed',sha256='0'*64)])):
            value=sample(); value.update(change)
            with self.subTest(change=change),self.assertRaises(ValueError): incident(value)
        self.enqueue()
        with self.assertRaises(ValueError): self.enqueue()
        self.root.chmod(0o755)
        with self.assertRaises(ValueError): self.store.snapshot()

    def test_shared_case_enumeration_retains_bounds_and_path_guards(self):
        self.enqueue(); self.assertEqual(len(self.store.case_ids(1)),1)
        value=sample('second'); value['summary']='A different synthetic case.'; self.enqueue(value)
        with self.assertRaises(ValueError): self.store.case_ids(1)
        for maximum in (0,True,-1):
            with self.subTest(maximum=maximum),self.assertRaises(ValueError): self.store.case_ids(maximum)
        folder=self.root/'cases'; unknown=folder/'not-a-case'; unknown.mkdir()
        with self.assertRaises(ValueError): self.store.case_ids(128)
        unknown.rmdir(); linked=folder/('f'*64); linked.symlink_to(self.temp.name,target_is_directory=True)
        with self.assertRaises(ValueError): self.store.case_ids(128)
        linked.unlink(); linked.write_text('not a directory')
        with self.assertRaises(ValueError): self.store.case_ids(128)


if __name__=='__main__': unittest.main()
