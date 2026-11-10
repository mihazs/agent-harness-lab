"""Independent synthetic annotation controls; no model, private traces or gold claims."""
from copy import deepcopy
import hashlib
import multiprocessing
from collections import Counter

import pytest

from agent_harness_lab.diagnostics.corpus import CorpusStore, incident
from agent_harness_lab.private_journal import record


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


class TestDiagnosticCorpus:
    @pytest.fixture(autouse=True)
    def corpus(self,tmp_path):
        self.tmp_path=tmp_path
        self.root=tmp_path/'corpus'; self.store=CorpusStore(self.root)

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
        assert item['state'] == 'pending'
        for reviewer in ('synthetic-reviewer-one','synthetic-reviewer-two'):
            item=self.update(item,'claimed',reviewer)
            packet=self.store.packet(case,reviewer)
            assert 'SUGGESTED_LABEL_MUST_STAY_HIDDEN' not in str(packet)
            assert 'label' not in packet['review_packet']
            assert not packet['prior_labels_visible']
            item=self.update(item,'submitted',reviewer,label())
        assert item['state'] == 'reviewed'; assert item['disposition'] == 'agreement'
        assert self.export()['records'] == []
        item=self.update(item,'adjudicated','synthetic-adjudicator',label(),'Synthetic independent declared review')
        assert item['state'] == 'adjudicated'
        result=self.export(); row,=result['records']
        assert row['annotation']['outcome'] == 'task_failure'
        assert not row['gold_label']; assert not result['classifier_selection_allowed']
        assert item['reviewer_independence'] == 'unverified'
        assert not item['task_outcome_oracle']; assert not result['publication_allowed']
        assert self.root.stat().st_mode&0o777 == 0o700

    def test_disagreements_and_original_labels_survive_adjudication(self):
        item=self.enqueue()
        for reviewer,annotation in (('one',label()),('two',label('successful','recovered'))):
            item=self.update(item,'claimed',reviewer); item=self.update(item,'submitted',reviewer,annotation)
        assert item['disposition'] == 'disagreement'
        item=self.update(item,'adjudicated','three',label('unknown','unknown'),'Contradictory synthetic evidence')
        assert item['disposition'] == 'unresolved'; assert self.export()['records'] == []
        events=self.store.history(self.store.case(item['case_id']))
        assert [e['payload']['label']['outcome'] for e,_ in events if e['kind']=='submitted'] == ['task_failure','successful']

    def test_self_review_wrong_claimant_and_stale_clients_cannot_overwrite(self):
        item=self.enqueue()
        with pytest.raises(ValueError): self.update(item,'claimed','SYNTHETIC-AUTHOR')
        claimed=self.update(item,'claimed','one')
        with pytest.raises(ValueError): self.update(item,'claimed','two')
        with pytest.raises(ValueError): self.update(claimed,'submitted','two',label())
        submitted=self.update(claimed,'submitted','one',label())
        with pytest.raises(ValueError): self.update(submitted,'claimed','ONE')
        claimed=self.update(submitted,'claimed','two'); reviewed=self.update(claimed,'submitted','two',label())
        with pytest.raises(ValueError): self.update(reviewed,'adjudicated','one',label(),'Self adjudication')

    def test_concurrent_claims_have_one_winner(self):
        item=self.enqueue(); ctx=multiprocessing.get_context('fork'); results=ctx.Queue()
        children=[ctx.Process(target=compete_claim,args=(str(self.root),item['case_id'],item['event_sha256'],r,results)) for r in ('one','two')]
        for child in children: child.start()
        for child in children:
            child.join(5); assert not child.is_alive(); assert child.exitcode == 0
        assert Counter(results.get(timeout=2) for _ in children) == Counter(['claimed','conflict'])
        results.close(); results.join_thread()

    def test_incomplete_capture_missing_evidence_and_permission_cannot_be_gold_outcome(self):
        for change in (dict(capture_complete=False),dict(capture_complete=None),dict(permission='unverified'),dict(evidence=[])):
            value=sample(); value.update(change); value['incident_id']+=str(len(list(self.root.glob('cases/*'))))
            item=self.enqueue(value); item=self.update(item,'claimed','one')
            with pytest.raises(ValueError): self.update(item,'submitted','one',label())
            unknown=label('unknown','unknown'); unknown.update(verification='unknown',evidence_refs=[],symptoms=[])
            item=self.update(item,'submitted','one',unknown)
            assert item['state'] == 'submitted'

    def test_outcome_distinctions_do_not_follow_keywords(self):
        for index,(outcome,recovery) in enumerate((('task_failure','unrecovered'),('successful','recovered'),
                ('external_failure','not_applicable'),('requirements_changed','not_applicable'),('successful','not_applicable'))):
            value=sample('synthetic-'+str(index)); value['summary']='Error failure timeout NOT a terminal outcome.'
            expected=label(outcome,recovery); self.reviewed(value,expected,expected,expected)
        rows=self.export()['records']
        assert Counter((r['annotation']['outcome'],r['annotation']['recovery']) for r in rows) == \
            Counter([('task_failure','unrecovered'),('successful','recovered'),('external_failure','not_applicable'),('requirements_changed','not_applicable'),('successful','not_applicable')])

    def test_new_roots_cannot_disguise_copied_protected_text(self):
        self.reviewed(sample('fitting','fitting'))
        self.enqueue(sample('heldout','validation'))
        with pytest.raises(ValueError, match='text aliases'): self.export()

    @pytest.mark.parametrize('key',['root_id','incident_id','source_revision_sha256'])
    def test_root_or_incident_alias_across_partitions_is_rejected(self,key):
        store=CorpusStore(self.tmp_path/'alias-corpus')
        first=sample('first','fitting'); store.enqueue(first)
        second=sample('second','validation'); second[key]=first[key]; second['summary']='Distinct synthetic wording.'
        if key=='incident_id':
            with pytest.raises(ValueError, match='explicit'): store.enqueue(second)
            return
        store.enqueue(second)
        with pytest.raises(ValueError, match='split leakage'):
            store.export_development(store.snapshot()['revision_sha256'])

    def test_exposure_retires_protected_partition_to_development(self):
        value=sample('protected','certification'); item=self.reviewed(value)
        assert self.export()['records'] == []
        item=self.update(item,'exposed',reason='Used in synthetic development diagnosis')
        assert item['partition'] == 'development'
        row,=self.export()['records']; assert row['record']['partition'] == 'certification'
        assert self.store.snapshot()['items'][0]['partition'] == 'development'
        changed=deepcopy(value); changed.update(rubric_sha256='b'*64,supersedes=item['case_id'])
        requeued=self.enqueue(changed)
        assert requeued['partition'] == 'development'
        assert self.store.packet(requeued['case_id'],'fresh-reviewer')['partition'] == 'development'

    def test_source_or_rubric_requeue_has_fresh_review_and_immutable_history(self):
        old=self.reviewed(); changed=sample(); changed.update(rubric_sha256='b'*64,supersedes=old['case_id'])
        new=self.enqueue(changed)
        assert new['state'] == 'pending'; assert new['submissions'] == 0
        assert self.export()['records'] == []
        assert len(self.store.snapshot()['items']) == 1
        unchanged=sample(); unchanged['supersedes']=old['case_id']
        with pytest.raises(ValueError, match='changed source/rubric'): self.enqueue(unchanged)
        case=self.store.case(old['case_id']); original=(case/'incident.json').read_bytes()
        assert record(case/'incident.json')[0]['rubric_sha256'] == 'a'*64
        (case/'incident.json').write_bytes(original+b' ')
        with pytest.raises(ValueError): self.store.snapshot()

    def test_stale_export_and_reimport_never_reuse_reviews(self):
        self.reviewed(); result=self.export(); revision=result['revision_sha256']
        value=sample('new'); value['summary']='Fresh independently synthetic text.'; self.enqueue(value)
        with pytest.raises(ValueError): self.store.export_development(revision)
        imported=CorpusStore(self.tmp_path/'imported'/'corpus'); item=imported.enqueue(result['records'][0]['record'])
        assert item['state'] == 'pending'
        assert imported.export_development(imported.snapshot()['revision_sha256'])['records'] == []

    def test_competing_requeues_and_unlinked_incident_revisions_are_rejected_atomically(self):
        old=self.enqueue(); first=sample(); first.update(rubric_sha256='b'*64,supersedes=old['case_id'])
        self.enqueue(first)
        second=sample(); second.update(rubric_sha256='c'*64,supersedes=old['case_id'])
        with pytest.raises(ValueError, match='competing'): self.enqueue(second)
        second['supersedes']=None
        with pytest.raises(ValueError, match='explicit'): self.enqueue(second)
        assert len(self.store.snapshot()['items']) == 1

    @pytest.mark.parametrize('key,new_value',[('partition','validation'),('root_id','renamed-root'),('family','renamed-family'),
            ('project_id','renamed-project'),('time_group','renamed-window'),('source_kind','public')])
    def test_requeues_cannot_relabel_partition_lineage_or_source_privacy(self,key,new_value):
        store=CorpusStore(self.tmp_path/('variant-'+key))
        old=store.enqueue(sample(partition='fitting'))
        changed=sample(partition='fitting')
        changed.update(rubric_sha256='b'*64,supersedes=old['case_id']); changed[key]=new_value
        with pytest.raises(ValueError, match='lineage/privacy/partition'):
            store.enqueue(changed)
        assert len(store.snapshot()['items']) == 1

    @pytest.mark.parametrize('change',[
        pytest.param(dict(schema_version=True),id='boolean-schema'),pytest.param(dict(extra='field'),id='extra-field'),
        pytest.param(dict(ambiguity=1),id='integer-ambiguity'),pytest.param(dict(summary='x'*4097),id='oversized-summary'),
        pytest.param(dict(evidence=[dict(id='x',content='changed',sha256='0'*64)]),id='changed-fragment')])
    def test_schema_bounds_duplicate_ids_changed_fragments_and_unsafe_store(self,change):
        value=sample(); value.update(change)
        with pytest.raises(ValueError): incident(value)
        self.enqueue()
        with pytest.raises(ValueError): self.enqueue()
        self.root.chmod(0o755)
        with pytest.raises(ValueError): self.store.snapshot()

    @pytest.mark.parametrize('maximum',[pytest.param(0,id='zero'),pytest.param(True,id='boolean'),pytest.param(-1,id='negative')])
    def test_shared_case_enumeration_retains_bounds_and_path_guards(self,maximum):
        self.enqueue(); assert len(self.store.case_ids(1)) == 1
        value=sample('second'); value['summary']='A different synthetic case.'; self.enqueue(value)
        with pytest.raises(ValueError): self.store.case_ids(1)
        with pytest.raises(ValueError): self.store.case_ids(maximum)
        folder=self.root/'cases'; unknown=folder/'not-a-case'; unknown.mkdir()
        with pytest.raises(ValueError): self.store.case_ids(128)
        unknown.rmdir(); linked=folder/('f'*64); linked.symlink_to(self.tmp_path,target_is_directory=True)
        with pytest.raises(ValueError): self.store.case_ids(128)
        linked.unlink(); linked.write_text('not a directory')
        with pytest.raises(ValueError): self.store.case_ids(128)

    @pytest.mark.parametrize('name',['export','snapshot','exported-record','item','packet'])
    def test_canonical_private_corpus_surfaces_cannot_be_relabelled_for_public_export(self,name):
        from agent_harness_lab.evaluation.benchmark import public_export_allowed, summary
        from tests.unit.evaluation import test_benchmark_contracts as benchmark_fixtures
        item=self.reviewed(); exported=self.export()
        surfaces=dict(export=exported,snapshot=self.store.snapshot(),**{'exported-record':exported['records'][0]},item=item,
                      packet=self.store.packet(item['case_id'],'synthetic-reader'))
        forged={**surfaces[name],**benchmark_fixtures.record(), 'publication_allowed':True}
        assert not public_export_allowed([forged])
        with pytest.raises(ValueError): summary([forged],public=True)

    @pytest.mark.parametrize('reviewer',[pytest.param(' synthetic-author',id='leading-space'),
        pytest.param('SYNTHETIC-AUTHOR ',id='uppercase-trailing-space'),pytest.param('\tsynthetic-author\n',id='tab-newline')])
    def test_declared_review_identity_cannot_use_padding_to_evade_separation(self,reviewer):
        store=CorpusStore(self.tmp_path/'padded'); item=store.enqueue(sample())
        with pytest.raises(ValueError):
            store.update(item['case_id'],item['event_sha256'],'claimed',reviewer=reviewer)
        value=sample(); value['author']=' synthetic-author '
        with pytest.raises(ValueError): incident(value)
        item=self.enqueue(); item=self.update(item,'claimed','one'); item=self.update(item,'submitted','one',label())
        with pytest.raises(ValueError): self.update(item,'claimed',' one ')
        item=self.update(item,'claimed','two'); item=self.update(item,'submitted','two',label())
        with pytest.raises(ValueError): self.update(item,'adjudicated',' one ',label(),'Padded reviewer')

