"""Independently synthetic local data; no foreign evaluators/providers are executed."""
from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import shutil
from unittest.mock import patch
import agent_harness_lab
from agent_harness_lab.cli import main
from agent_harness_lab.evaluation import interchange as module
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.evaluation.regressions import load
if __package__:
    from . import test_regression_review as review_fixtures
else:
    import test_regression_review as review_fixtures


def digest(raw): return hashlib.sha256(raw).hexdigest()


class InterchangeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.inputs=self.root/'inputs'; self.inputs.mkdir()
        self.source=self.inputs/'source.json'; self.mapping=self.inputs/'mapping.json'
        self.output=self.root/'imported'; self.registry=None
        self.values=[dict(vars={'question':'Return the synthetic numeral four'},
            **{'assert':[dict(type='equals',value='4'),dict(type='contains',value='4',weight=0)],'threshold':.5})]
        self.value=dict(schema_version=1,format=module.FORMATS[0],source=dict(repository='synthetic:independent-public-fixture',
            revision='a'*40,format_version='inline-json-subset-v1',dataset_id='synthetic-four',source_kind='synthetic',
            permission='approved',dataset_license=dict(id='CC0-1.0',ref='synthetic:fixture-author-dedication'),sha256='a'*64),
            review=dict(identity='synthetic-operator',source='operator_attestation',evidence_refs=[dict(ref='synthetic:review',sha256='b'*64)]),
            runtime=dict(provider=None,model_revision=None,configuration_sha256=None),score_descriptor=None,registry_sha256=None,
            items=[dict(record=1,source_item_id='four',task_id='four',root_id='related-four',family='synthetic-text',
                source_split='foreign-dev',partition='construction',exposure='development',expected_boundary='Return the numeral four.')])
        self.write()

    def write(self):
        raw=(json.dumps(self.values)+'\n').encode() if self.value['format']==module.FORMATS[0] else b''.join(
            (json.dumps(v)+'\n').encode() for v in self.values)
        self.source.write_bytes(raw); self.value['source']['sha256']=digest(raw)
        self.mapping.write_text(json.dumps(self.value))

    def import_data(self, output=None):
        return module.import_local(self.mapping,self.source,output or self.output,registry_path=self.registry)

    def saved(self): return json.loads((self.output/'claims.json').read_bytes())

    def openai(self):
        self.value['format']=module.FORMATS[2]; self.values=[dict(input=[dict(role='user',content='Return four.')],ideal=['4'])]
        self.registry=self.inputs/'registry.json'
        self.registry.write_text(json.dumps(dict(eval_id='synthetic.dev.v1',**{'class':'evals.elsuite.basic.match:Match'},
            args=dict(samples_jsonl='never-follow/samples.jsonl'),metrics=['accuracy'])))
        self.value['registry_sha256']=digest(self.registry.read_bytes()); self.write()

    def test_actual_cli_promptfoo_import_produces_existing_unknown_proposal_without_execution(self):
        stdout=io.StringIO()
        with patch('socket.socket',side_effect=AssertionError('no network')), \
                patch('subprocess.Popen',side_effect=AssertionError('no execution')),redirect_stdout(stdout):
            code=main(['bank-interchange','import','--mapping',str(self.mapping),'--source',str(self.source),'--output',str(self.output)])
        self.assertEqual(code,0); report=json.loads(stdout.getvalue())
        self.assertEqual(self.saved()['records'][0]['foreign_claims'],self.values[0])
        source=next((self.output/'proposals').glob('*.json')); proposal,pin=load(source)
        receipt=proposal.receipt(pin)
        self.assertEqual(receipt['declared_disposition'],'insufficient_evidence'); self.assertFalse(receipt['bank_admission'])
        self.assertFalse(report['publication_allowed']); self.assertEqual(report['independence'],'unverified')
        self.assertIn('before.harness_sha256',receipt['missing_evidence'])
        self.assertEqual(self.output.stat().st_mode&0o777,0o700)
        for path in self.output.rglob('*'):
            self.assertEqual(path.stat().st_mode&0o777,0o700 if path.is_dir() else 0o600)
        with redirect_stdout(io.StringIO()): self.assertEqual(main(['regression','--proposal',str(source)]),0)

    def test_openai_registry_samples_preserve_prefix_reference_semantics_without_class_loading(self):
        self.openai()
        with patch('socket.socket',side_effect=AssertionError('no network')),patch('subprocess.Popen',side_effect=AssertionError('no execution')):
            report=self.import_data()
        self.assertEqual(report['reference_semantics'],'prefix_match_reported_not_executed')
        self.assertEqual(self.saved()['records'][0]['foreign_claims']['ideal'],['4'])
        self.assertTrue('4 extra'.startswith('4')); self.assertNotEqual('4 extra','4')
        self.assertFalse((self.inputs/'never-follow').exists())
        self.assertEqual(len(list((self.output/'proposals').glob('*.json'))),1)

    def test_foreign_weighted_components_and_contradictions_are_claims_not_boolean_grades(self):
        self.value['format']=module.FORMATS[1]
        self.value['score_descriptor']=dict(metric='foreign-weighted',kind='aggregate',scale=dict(minimum=0,maximum=1,unit='ratio'),
            aggregation='case',judge_ref=None)
        self.values=[dict(testIdx=0,promptIdx=0,success=False,score=1/3,response=dict(output='Goodbye world'),
            gradingResult={'pass':False,'score':1/3,'componentResults':[
                {'pass':False,'score':0,'assertion':dict(type='equals',value='Hello world',weight=2)},
                {'pass':True,'score':1,'assertion':dict(type='contains',value='world',weight=1)}]})]
        self.write(); self.import_data(); row=self.saved()['records'][0]
        self.assertEqual(row['foreign_claims'],self.values[0]); self.assertTrue(bool(row['foreign_claims']['score']))
        self.assertFalse(row['foreign_claims']['success']); self.assertIsNone(row['ahl_outcome'])
        self.assertEqual(row['comparison_status'],'not_comparable'); self.assertFalse((self.output/'proposals').exists())

    def test_identical_reimport_is_idempotent_changed_bytes_mapping_registry_have_new_identity(self):
        first=self.import_data(); frozen={str(p.relative_to(self.output)):p.read_bytes() for p in self.output.rglob('*') if p.is_file()}
        self.assertEqual(first,self.import_data()); self.assertEqual(frozen,{str(p.relative_to(self.output)):p.read_bytes() for p in self.output.rglob('*') if p.is_file()})
        self.value['items'][0]['expected_boundary']='A different declared boundary.'; self.write()
        self.assertNotEqual(first['import_sha256'],self.import_data(self.root/'changed')['import_sha256'])
        with self.assertRaises(ValueError): self.import_data()
        self.openai(); before=self.import_data(self.root/'registry-one')
        value=json.loads(self.registry.read_bytes()); value['eval_id']='synthetic.dev.v2'; self.registry.write_text(json.dumps(value))
        self.value['registry_sha256']=digest(self.registry.read_bytes()); self.write()
        self.assertNotEqual(before['import_sha256'],self.import_data(self.root/'registry-two')['import_sha256'])

    def test_selected_layout_import_is_actual(self):
        expected=os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
        if expected: self.assertTrue(Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve()))

    def test_known_private_markers_reject_forged_public_provenance(self):
        report=self.import_data(); claims=self.saved()
        public=dict(source_kind='synthetic',approved_publication=True,source_ref='synthetic:public',derived_from_private=False)
        proposal=json.loads(next((self.output/'proposals').glob('*.json')).read_bytes())
        for value in (report,claims,*claims['records'],self.value,proposal):
            for wrapper in (None,'receipt','analysis','context'):
                forged=dict(value,publication_allowed=True,provenance=public)
                row=forged if wrapper is None else {wrapper:forged,'provenance':public}
                self.assertFalse(public_export_allowed([row]))

    def test_both_case_profiles_require_supplied_reproduction_then_existing_review_admission_and_export(self):
        from agent_harness_lab.evaluation.regression_review import ReviewStore
        from agent_harness_lab.evaluation.bank_tools import export_manifest
        from agent_harness_lab.evaluation.task_bank import validate
        for format_name in (module.FORMATS[0],module.FORMATS[2]):
            with self.subTest(format=format_name):
                fixture=review_fixtures.ReviewFixture(); fixture.setUp()
                try:
                    replay=fixture.fixture; provided=json.loads(replay.source.read_bytes())
                    descriptor=json.loads(replay.draft.read_bytes()); task=descriptor['task']
                    self.value['items'][0].update(task_id=task['task_id'],root_id=task['root_id'],family=task['family'],
                        expected_boundary=provided['expected_boundary'])
                    prompt=json.loads((replay.root/'fixture/task.json').read_bytes())['prompt']
                    if format_name==module.FORMATS[2]:
                        self.openai(); self.values[0]['input']=prompt
                    else:
                        self.value.update(format=format_name,registry_sha256=None); self.registry=None
                        self.values=[dict(vars={'question':prompt},**{'assert':[dict(type='llm-rubric',value=provided['expected_boundary'])]})]
                    self.write(); destination=self.root/format_name; self.import_data(destination)
                    imported=next((destination/'proposals').glob('*.json')); raw_imported=imported.read_bytes()
                    declared=json.loads(raw_imported); replay.source.write_bytes(raw_imported)
                    descriptor['task']['source_ref']='external-evaluation:'+declared['proposal_id']
                    replay.draft.write_text(json.dumps(descriptor)); replay.bind()
                    incomplete=ReviewStore(replay.root/'incomplete-import')
                    reported=incomplete.propose(replay.source,replay.draft,replay.spec,'synthetic-author')
                    with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=fixture.backend):
                        failed=incomplete.reproduce(reported['case_id'],fixture.backend)
                    self.assertEqual(failed['state'],'reported'); fixture.backend.run.assert_not_called()
                    # These bytes are provided by the independent synthetic fixture factory,
                    # never inferred from a foreign ideal, pass label or score.
                    for key in ('before','after','task_start','environment','reproduction'): declared[key]=provided[key]
                    declared['source']['trace_refs']=provided['source']['trace_refs']
                    replay.source.write_text(json.dumps(declared)); replay.bind()
                    store=ReviewStore(replay.root/'complete-import')
                    case=store.propose(replay.source,replay.draft,replay.spec,'synthetic-author')['case_id']
                    with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=fixture.backend):
                        observed=store.reproduce(case,fixture.backend)
                    self.assertEqual(observed['state'],'reproduced'); self.assertEqual(fixture.backend.run.call_count,21)
                    event=next(e for e,_ in store.history(store.case(case)) if e['kind']=='reproduced')
                    actual=json.loads((store.case(case)/event['payload']['attempt']/'replay/replay.json').read_bytes())
                    self.assertFalse(actual['phases']['ab']['outcomes']['recorded/baseline']['outcome'])
                    self.assertTrue(actual['phases']['ab']['outcomes']['recorded/candidate']['outcome'])
                    current=store.draft(case)
                    review=json.loads(fixture.review_file().read_bytes())
                    review.update(case_id=case,expected_event_sha256=current['event_sha256'],evidence_sha256=current['evidence_sha256'])
                    review_path=replay.root/'new-review.json'; review_path.write_text(json.dumps(review)); store.review(case,review_path)
                    parent=replay.root/'parent'; source=Path(agent_harness_lab.__file__).parent/'evaluation/fixtures/starter-v1'
                    shutil.copytree(source,parent); bank=json.loads((parent/'bank.json').read_bytes())
                    bank['tasks']=[t for t in bank['tasks'] if t['task_id']=='resumption']; (parent/'bank.json').write_text(json.dumps(bank))
                    current=store.show(case)
                    request=dict(schema_version=1,case_id=case,expected_event_sha256=current['event_sha256'],
                        evidence_sha256=current['evidence_sha256'],parent_bank_sha256=digest((parent/'bank.json').read_bytes()),
                        parent_revision_sha256=None,operator=dict(identity='synthetic-curator',source='operator_attestation',
                            provenance='Provided synthetic artifact review',basis='Review actual frozen controls and replay.'),
                        purpose='development_regression',provenance_binding=None,reason='Synthetic data-only workflow control.')
                    request_path=replay.root/'accept.json'; request_path.write_text(json.dumps(request))
                    accepted=store.accept(case,parent/'bank.json',request_path)
                    admitted=store.root/'revisions'/accepted['revision_sha256']/'bank.json'
                    exported=export_manifest(admitted,replay.root/'export')
                    self.assertFalse(validate(admitted)['certification_eligible']); self.assertFalse(exported['independence_verified'])
                    self.assertEqual(imported.read_bytes(),raw_imported)
                    self.assertEqual(observed['historical_verification'],'unverified')
                finally: fixture.doCleanups()

    def test_executable_sensitive_templates_and_remote_config_reject_before_output(self):
        original=deepcopy(self.values)
        forbidden=[dict(providers=['synthetic:must-not-run']),dict(options={'provider':'synthetic:must-not-run'}),
            dict(assertScoringFunction='file://never.py'),dict(vars={'api_key':'SYNTHETIC_SECRET'}),
            dict(vars={'question':'file://never-read.txt'}),dict(vars={'question':'{{ unsafe() }}'}),
            {'assert':[dict(type='javascript',value='throw new Error("never")')]},
            {'assert':[dict(type='python',value='file://never.py')]},
            {'assert':[dict(type='equals',value='https://synthetic.invalid/not-fetched')]}]
        for edit in forbidden:
            self.values=[dict(original[0],**edit)]; self.write()
            with self.subTest(edit=edit),patch('socket.socket',side_effect=AssertionError('no network')), \
                    patch('subprocess.Popen',side_effect=AssertionError('no execution')),self.assertRaises(ValueError):
                self.import_data()
            self.assertFalse(self.output.exists())

    def test_mapping_permission_license_lineage_ids_and_versions_fail_closed(self):
        original=deepcopy(self.value)
        edits=[lambda v:v.update(schema_version=True),lambda v:v.update(format='future-profile'),
            lambda v:v['source'].update(permission='unverified'),lambda v:v['source'].update(sha256='f'*64),
            lambda v:v['source']['dataset_license'].update(id='unknown'),lambda v:v['source'].pop('dataset_license'),
            lambda v:v['review'].update(source='verified'),lambda v:v['items'][0].update(partition='certification'),
            lambda v:v['items'][0].update(exposure='untouched'),lambda v:v['items'][0].update(record=True),
            lambda v:v['items'].append(deepcopy(v['items'][0]))]
        for edit in edits:
            self.value=deepcopy(original); edit(self.value); self.mapping.write_text(json.dumps(self.value))
            with self.subTest(edit=edit),self.assertRaises((ValueError,KeyError)): self.import_data()
            self.assertFalse(self.output.exists())

    def test_malformed_duplicate_nonfinite_surrogate_deep_truncated_oversize_and_links_reject(self):
        original=self.source.read_bytes()
        payloads=[b'[{"vars":{},"vars":{},"assert":[]}]',b'[{"vars":{"x":NaN},"assert":[]}]',
            b'[{"vars":{"x":"\\ud800"},"assert":[]}]',b'['*64+b'{}'+b']'*64,original[:-2],b' '*(module.MAX_BYTES+1)]
        for raw in payloads:
            self.source.write_bytes(raw); self.value['source']['sha256']=digest(raw); self.mapping.write_text(json.dumps(self.value))
            with self.subTest(raw=raw[:32]),self.assertRaises(ValueError): self.import_data()
            self.assertFalse(self.output.exists())
        self.source.write_bytes(original); self.value['source']['sha256']=digest(original); self.mapping.write_text(json.dumps(self.value))
        target=self.inputs/'target.json'; self.source.rename(target); self.source.symlink_to(target)
        with self.assertRaises(ValueError): self.import_data()
        self.source.unlink(); os.link(target,self.source)
        with self.assertRaises(ValueError): self.import_data()

    def test_changed_source_during_capture_cannot_publish_and_changed_snapshot_cannot_reimport(self):
        original=module._capture
        def changed(*args):
            result=original(*args); self.source.write_bytes(self.source.read_bytes()+b' '); return result
        with patch.object(module,'_capture',side_effect=changed),self.assertRaises(ValueError): self.import_data()
        self.assertFalse(self.output.exists()); self.assertFalse(list(self.root.glob('.interchange-*')))
        self.write(); self.import_data(); (self.output/'source.data').write_bytes(b'changed')
        with self.assertRaises(ValueError): self.import_data()

    def test_openai_unknown_class_missing_metadata_and_stale_registry_reject_without_loading(self):
        self.openai(); original=self.registry.read_bytes()
        for change in (dict(**{'class':'synthetic.module:RunNever'}),dict(args={'samples_jsonl':'x','setup':'never'})):
            value=json.loads(original); value.update(change); self.registry.write_text(json.dumps(value))
            self.value['registry_sha256']=digest(self.registry.read_bytes()); self.write()
            with self.assertRaises(ValueError): self.import_data()
            self.assertFalse(self.output.exists())
        self.registry.write_bytes(original); self.value['registry_sha256']='f'*64; self.write()
        with self.assertRaises(ValueError): self.import_data()

    def test_result_missing_components_remain_visible_and_boolean_integer_aliases_reject(self):
        self.value['format']=module.FORMATS[1]
        self.value['score_descriptor']=dict(metric='reported',kind='unknown',scale=dict(minimum=None,maximum=None,unit='source_units'),
            aggregation='case',judge_ref=None)
        self.values=[dict(testIdx=0,promptIdx=0,success=True,score=-.8,gradingResult=None)]
        self.write(); report=self.import_data()
        self.assertTrue(any(v.endswith(':gradingResult') for v in report['missing_fields']))
        self.assertEqual(self.saved()['records'][0]['foreign_claims']['score'],-.8)
        original=deepcopy(self.values)
        for key,value in (('score',True),('success',1),('testIdx',False)):
            self.values=deepcopy(original); self.values[0][key]=value; self.write()
            with self.subTest(key=key),self.assertRaises(ValueError): self.import_data(self.root/'invalid')

    def test_related_result_rows_retain_one_declared_task_without_independence_inflation(self):
        self.value['format']=module.FORMATS[1]
        self.value['score_descriptor']=dict(metric='reported',kind='aggregate',scale=dict(minimum=None,maximum=None,unit='source_units'),
            aggregation='case',judge_ref='synthetic:unverified-judge')
        self.values=[dict(testIdx=0,promptIdx=i,success=True,score=.2+i/2) for i in range(2)]
        second=deepcopy(self.value['items'][0]); second.update(record=2,source_item_id='four-other-prompt')
        self.value['items'].append(second); self.write()
        report=self.import_data(); rows=self.saved()['records']
        self.assertEqual(report['items'],2); self.assertEqual(len({r['mapping']['task_id'] for r in rows}),1)
        self.assertEqual(len({r['mapping']['root_id'] for r in rows}),1)
        self.assertEqual(report['independence'],'unverified'); self.assertNotIn('independent_roots',report)
        self.values[1]['promptIdx']=0; self.write()
        with self.assertRaises(ValueError): self.import_data(self.root/'ambiguous')

    def test_engine_changes_invalidate_reimport_and_no_inspection_or_failed_cli_output_leaks_content(self):
        first=self.import_data()
        from agent_harness_lab.evaluation.decision_receipts import engine
        changed=dict(engine(),synthetic_upgrade=True)
        with patch('agent_harness_lab.evaluation.decision_receipts.engine',return_value=changed):
            self.assertNotEqual(first['import_sha256'],module.inspect(self.mapping,self.source)['import_sha256'])
            with self.assertRaises(ValueError): self.import_data()
        sentinel='SYNTHETIC_PRIVATE_INPUT_SENTINEL'; self.values[0]['vars']['question']=sentinel; self.write()
        stdout=io.StringIO()
        with redirect_stdout(stdout): self.assertEqual(main(['bank-interchange','inspect','--mapping',str(self.mapping),'--source',str(self.source)]),0)
        self.assertNotIn(sentinel,stdout.getvalue()); self.assertNotIn(str(self.root),stdout.getvalue())

    def test_frozen_output_modes_and_empty_directory_inventory_cannot_change(self):
        first=self.import_data()
        for kind in ('file-executable','directory-world-access','unexpected-empty-directory'):
            try:
                if kind=='file-executable': (self.output/'source.data').chmod(0o700)
                elif kind=='directory-world-access': (self.output/'proposals').chmod(0o777)
                else: (self.output/'extra').mkdir(mode=0o700)
                with self.subTest(kind=kind),self.assertRaises(ValueError): self.import_data()
            finally:
                (self.output/'source.data').chmod(0o600); (self.output/'proposals').chmod(0o700)
                if (self.output/'extra').exists(): (self.output/'extra').rmdir()
            self.assertEqual(first,self.import_data())
