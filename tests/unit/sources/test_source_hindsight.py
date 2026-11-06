"""Synthetic Hindsight v1.1.0 bank archives; memories are bank-scoped contextual claims, never task outcomes."""
import contextlib
import copy
import io
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.sources import hindsight
from agent_harness_lab.sources.hardened import HostileReport
from agent_harness_lab.sources.hindsight import BankClaim, HindsightArchive
from agent_harness_lab.sources.schema import SourceSpec, digest

SECRET = 'SYNTHETIC_SECRET_TOKEN'
MANIFEST = dict(schema_version=1, source_bank_id='fixture-bank', exported_at='2031-05-01T00:00:00+00:00', document_count=2,
    fact_count=3, observation_count=1, mental_model_count=0, knowledge_page_count=0, archive_type='bank',
    invalidated_memory_count=1)
ALPHA = dict(id='doc-alpha', original_text='invented retained text '+SECRET, tags=['invented-tag'],
    created_at='2031-04-01T00:00:00+00:00', chunks=[dict(chunk_index=0, chunk_text='invented chunk')], facts=[
        dict(text='invented office is in Lisbon', source_id='unit-1', fact_type='world', occurred_start='2030-01-01T00:00:00Z',
             occurred_end='2031-03-01T00:00:00Z', mentioned_at='2031-04-01T00:00:00Z', created_at='2031-04-01T00:00:01Z',
             entities=['invented-office']),
        dict(text='invented office moved to Porto', source_id='unit-2', fact_type='world', occurred_start='2031-03-01T00:00:00Z',
             mentioned_at='2031-04-01T00:00:00Z', created_at='2031-04-01T00:00:02Z',
             causal_relations=[dict(relation_type='caused_by', target_fact_index=0)])])
BETA = dict(id='doc-beta', original_text='invented session', created_at='2031-04-03T00:00:00+00:00', facts=[
    dict(text='all tests pass '+SECRET, source_id='unit-3', fact_type='experience', created_at='2031-04-03T00:00:01Z')])
OBSERVATIONS = [dict(source_id='obs-1', text='invented consolidated office history', created_at='2031-04-04T00:00:00Z',
    proof_count=2, sources=[dict(document_id='doc-alpha', fact_index=1), dict(document_id='doc-beta', fact_index=0)])]
INVALIDATED = [dict(id='unit-0', bank_id='fixture-bank', document_id='doc-alpha', text='invented office is in Faro',
    fact_type='world', occurred_start='2030-01-01T00:00:00Z', created_at='2031-04-01T00:00:00Z',
    invalidated_at='2031-04-02T00:00:00Z', invalidation_reason='invented correction '+SECRET)]
PARTS = {'manifest.json': MANIFEST, 'documents/000000.json': ALPHA, 'documents/000001.json': BETA,
         'observations.json': OBSERVATIONS, 'data/invalidated_memory_units.json': INVALIDATED}
LEAKS = (SECRET, 'fixture-bank', 'doc-alpha', 'unit-1', 'obs-1', 'invented', 'exports/bank-a')


class HindsightSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.write()

    def write(self, parts=None, **selection):
        archive = self.root/'exports/bank-a'
        for name, value in (PARTS if parts is None else parts).items():
            (archive/name).parent.mkdir(parents=True, exist_ok=True)
            (archive/name).write_text(value if isinstance(value, str) else json.dumps(value))
        self.selection = dict(dict(schema_version=1, hindsight_version='1.1.0', instance='fixture-instance',
            bank_id='fixture-bank', archive='exports/bank-a'), **selection)
        (self.root/'selection.json').write_text(json.dumps(self.selection))
        self.source = SourceSpec('local_file', 'hindsight-bank-archive', 'v1', digest(str(self.root.resolve())),
            digest('synthetic-project'), digest(self.selection), identity_provenance='source_declared_id', enabled=True)
        self.manifest = self.root/'source.json'
        self.manifest.write_text(json.dumps(dict(schema_version=1, source=self.source.to_dict(), data='selection.json')))
        self.connector = HindsightArchive(self.manifest)

    def read(self, parts=None, **selection):
        if parts is not None or selection: self.write(dict(PARTS, **(parts or {})), **selection)
        return self.connector.read(self.source)

    def claims(self, kind):
        return [c for c in self.connector.claims if c.claim_type == kind]

    def invoke(self, operation):
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            code = main(['sources', operation, '--manifest', str(self.manifest), '--project-sha256', self.source.project_sha256,
                '--scope-sha256', self.source.scope_sha256, '--cache', str(self.root/'private/cache.json')])
        for leak in LEAKS: self.assertNotIn(leak, output.getvalue()+error.getvalue())
        return code, output.getvalue()

    def test_each_archive_part_becomes_its_own_claim_and_record(self):
        snapshot = self.read()
        summary = self.connector.summary
        self.assertEqual(summary['claims'], dict(retained_document=2, extracted_fact=3, fact_relation=1,
                                                 consolidated_observation=1, invalidated_fact=1))
        self.assertEqual(len(snapshot.records), 9)
        provenance = {r.record_type: r.provenance for r in snapshot.records}
        self.assertEqual(provenance, dict(hindsight_retained_document='native_field', hindsight_extracted_fact='model_hypothesis',
            hindsight_fact_relation='model_hypothesis', hindsight_consolidated_observation='model_hypothesis',
            hindsight_invalidated_fact='model_hypothesis', hindsight_archive='native_field'))
        for record in snapshot.records:
            self.assertEqual((record.plane, record.causal_status, record.independence, record.capture_completeness),
                             ('contextual_assertion', 'unknown', 'unknown', 'partial'))
        relation, = self.claims('fact_relation')
        facts = self.claims('extracted_fact')
        self.assertEqual((relation.detail, relation.target_native_id_sha256), ('caused_by', facts[0].native_id_sha256))
        observation, = self.claims('consolidated_observation')
        self.assertEqual((observation.source_count, observation.synthesizes_multiple_origins), (2, True))
        document = self.claims('retained_document')[0]
        self.assertEqual({c.document_sha256 for c in (*facts[:2], relation, *self.claims('invalidated_fact'))},
                         {document.document_sha256})
        # The clean fixture is the baseline every warning test is compared against.
        self.assertEqual(summary['warnings'], ['archive_version_unstated', 'facts_are_model_extractions',
                                               'invalidations_are_review_candidates', 'observations_not_independent'])

    def test_temporal_corrections_separate_current_historical_and_invalidated_assertions(self):
        self.read()
        old, moved, session = self.claims('extracted_fact')
        retracted, = self.claims('invalidated_fact')
        self.assertEqual([c.assertion_status for c in (old, moved, session, retracted)],
                         ['historical', 'current', 'current', 'invalidated'])
        self.assertEqual([c.review_candidate for c in (old, moved, session, retracted)], [True, False, False, True])
        # Record observed time and fact validity stay distinct fields.
        self.assertEqual((old.recorded_at, old.mentioned_at, old.valid_from, old.valid_until),
            ('2031-04-01T00:00:01+00:00', '2031-04-01T00:00:00+00:00', '2030-01-01T00:00:00+00:00', '2031-03-01T00:00:00+00:00'))
        self.assertEqual((retracted.invalidated_at, retracted.valid_from), ('2031-04-02T00:00:00+00:00', '2030-01-01T00:00:00+00:00'))
        self.assertEqual(self.connector.summary['assertions'], dict(current=3, historical=1, not_yet_valid=0, invalidated=1,
                                                                    undetermined=0, not_applicable=3))
        future = copy.deepcopy(BETA); future['facts'][0]['occurred_start'] = '2032-01-01T00:00:00Z'
        self.read({'documents/000001.json': future})
        self.assertEqual(self.claims('extracted_fact')[2].assertion_status, 'not_yet_valid')
        # Without a trustworthy export instant nothing is current or historical.
        self.read({'manifest.json': dict(MANIFEST, exported_at='2031-05-01T00:00:00')})
        self.assertEqual({c.assertion_status for c in self.claims('extracted_fact')}, {'undetermined'})
        self.assertIn('export_time_unverified', self.connector.summary['warnings'])
        # Upstream's year-9999 open bound is no instant, and an inverted window is never guessed.
        open_end, inverted = copy.deepcopy(ALPHA), copy.deepcopy(ALPHA)
        open_end['facts'][0]['occurred_end'] = '9999-12-31T23:59:59Z'
        inverted['facts'][0]['occurred_end'] = '2029-01-01T00:00:00Z'
        self.read({'documents/000000.json': open_end})
        self.assertEqual((self.claims('extracted_fact')[0].valid_until, self.claims('extracted_fact')[0].assertion_status),
                         (None, 'current'))
        self.assertEqual(self.connector.summary['timestamps']['valid_until']['placeholder'], 1)
        self.read({'documents/000000.json': inverted})
        self.assertEqual(self.claims('extracted_fact')[0].assertion_status, 'undetermined')
        self.assertIn('inverted_validity', self.connector.summary['warnings'])

    def test_claims_never_become_outcomes_defects_or_retrieval_evidence(self):
        self.read()
        for claim in self.connector.claims:
            self.assertEqual((claim.independent_case, claim.evidence_of_use, claim.task_outcome_verified,
                              claim.agent_defect_verified, claim.native_session_verified), (False,)*5)
        retracted, = self.claims('invalidated_fact')
        for change in (dict(task_outcome_verified=True), dict(agent_defect_verified=True), dict(review_candidate=False),
                       dict(assertion_status='current'), dict(claim_type='extracted_fact'), dict(valid_from='2031-01-01T00:00:00')):
            with self.subTest(change=change):
                with self.assertRaises(ValueError): BankClaim(**dict(retracted.to_dict(), **change))
        summary = self.connector.summary
        self.assertEqual(summary['operations'], dict(retain='refused_write', recall='refused_inference',
            reflect='refused_generation', transfer_export='not_called', memories_list='not_called', mental_models='not_read'))
        for key in ('task_outcomes_verified', 'evidence_of_use', 'retrieval_scores_used', 'publication_allowed', 'network_used',
                    'service_contacted', 'database_opened', 'inference_invoked', 'bodies_stored'):
            self.assertIs(summary[key], False)
        self.assertEqual((summary['compatibility'], summary['upstream_shape'], summary['version_attestation'],
                          summary['upstream_commit']), ('unverified', 'source_inferred', 'caller_declared',
                          'a5a619b5ca59c5c39e0f96be6f7a31fabd136bd5'))
        self.assertEqual(summary['native_session_correlation'], 'not_attempted')
        self.assertFalse(public_export_allowed([summary]))
        self.assertFalse(public_export_allowed([dict(summary, publication_allowed=True,
            provenance=dict(source_kind='synthetic', approved_publication=True, source_ref='x', derived_from_private=False))]))

    def test_another_bank_is_blocked_before_any_document_is_read(self):
        opened = []
        actual = hindsight.read_regular
        def read(path, limit): opened.append(Path(path).name); return actual(path, limit)
        self.write(bank_id='other-bank')
        with patch.object(hindsight, 'read_regular', side_effect=read):
            with self.assertRaisesRegex(PermissionError, 'another bank'): self.connector.read(self.source)
            self.assertEqual(opened, ['manifest.json'])
            code, output = self.invoke('preview')
        # Recognized, then blocked: no probe result or snapshot is printed.
        self.assertEqual((code, output), (3, ''))
        self.assertFalse((self.root/'private').exists())
        foreign = [dict(INVALIDATED[0], bank_id='other-bank')]
        with self.assertRaisesRegex(PermissionError, 'another bank'):
            self.read({'data/invalidated_memory_units.json': foreign})

    def test_only_manifest_named_files_are_read_and_missing_parts_reject(self):
        opened = []
        actual = hindsight.read_regular
        def read(path, limit): opened.append(str(path)); return actual(path, limit)
        self.write(dict(PARTS, **{'documents/000002.json': ALPHA, 'mental_models.json': []}))
        with patch.object(hindsight, 'read_regular', side_effect=read): self.read()
        self.assertFalse([p for p in opened if p.endswith(('000002.json', 'mental_models.json'))])
        self.read({'manifest.json': dict(MANIFEST, mental_model_count=2, includes_history=True)})
        self.assertTrue({'mental_models_not_read', 'history_not_read'} <= set(self.connector.summary['warnings']))
        with self.assertRaises(FileNotFoundError): self.read({'manifest.json': dict(MANIFEST, document_count=4)})

    def test_unsupported_versions_and_archive_shapes_are_refused(self):
        for version in ('1.2.0', '1.1', '', None):
            with self.subTest(version=version):
                with self.assertRaisesRegex(ValueError, 'unsupported Hindsight release'): self.read(hindsight_version=version)
        for manifest in (dict(MANIFEST, schema_version=2), dict(MANIFEST, archive_type='tenant'),
                         dict(MANIFEST, document_count=-1), dict(MANIFEST, source_bank_id='')):
            with self.subTest(manifest=manifest):
                with self.assertRaises(ValueError): self.read({'manifest.json': manifest})
        with self.assertRaises(ValueError): self.connector.read(SourceSpec(**dict(self.source.to_dict(), version='v2')))

    def test_identity_relation_and_source_gaps_are_explicit(self):
        alpha = copy.deepcopy(ALPHA)
        alpha['facts'][0].pop('source_id'); alpha['facts'][0]['invented_column'] = SECRET
        alpha['facts'][1]['causal_relations'] = [dict(relation_type='caused_by', target_fact_index=5),
                                                 dict(relation_type='invented_link', target_fact_index=0)]
        beta = dict(BETA, id='doc-alpha')
        observations = [dict(OBSERVATIONS[0], sources=[dict(document_id='doc-gamma', fact_index=0)])]
        invalidated = [dict(INVALIDATED[0], id='unit-2'), dict(INVALIDATED[0], id=None)]
        self.read({'documents/000000.json': alpha, 'documents/000001.json': beta, 'observations.json': observations,
                   'data/invalidated_memory_units.json': invalidated})
        summary = self.connector.summary
        self.assertEqual(summary['rows_rejected'], dict(document=0, relation=1, invalidated=1))
        self.assertEqual(summary['rows_with_unknown_fields']['fact'], 1)
        self.assertTrue({'fact_without_source_id', 'relation_target_unresolved', 'unrecognized_relation_type',
            'duplicate_native_id', 'observation_source_unresolved', 'invalidated_fact_still_live', 'row_without_native_identity',
            'unknown_row_fields', 'declared_total_mismatch'} <= set(summary['warnings']))
        self.assertEqual([c.occurrence for c in self.claims('retained_document')], [0, 1])
        self.assertNotIn(SECRET, json.dumps(summary))

    def test_repeated_sources_and_missing_observation_ids_are_warned_precisely(self):
        base = OBSERVATIONS[0]['sources'][0]
        self.read({'observations.json': [dict(OBSERVATIONS[0], sources=[base, dict(base)])]})
        self.assertNotIn('observation_source_unresolved', self.connector.summary['warnings'])
        observation = dict(OBSERVATIONS[0]); observation.pop('source_id')
        self.read({'observations.json': [observation]})
        self.assertIn('observation_without_source_id', self.connector.summary['warnings'])
        self.read({'observations.json': OBSERVATIONS})
        self.assertNotIn('observation_without_source_id', self.connector.summary['warnings'])

    def test_malformed_and_oversized_archives_are_rejected(self):
        for name, raw in (('manifest.json', 'not json'), ('documents/000000.json', '[]'), ('observations.json', '{}'),
                          ('documents/000001.json', json.dumps(dict(BETA, facts=['row']))),
                          ('documents/000001.json', '{"id": "a", "id": "b"}'),
                          ('documents/000000.json', json.dumps(dict(ALPHA, facts=[dict(text='x', causal_relations=3)]))),
                          *(('documents/000000.json', json.dumps(dict(ALPHA, facts=[dict(text='x', causal_relations=bad)])))
                            for bad in ({}, '', 0, False))):
            with self.subTest(name=name, raw=raw[:30]):
                with self.assertRaises(ValueError): self.read({name: raw})
        for bound in (dict(max_rows=8), dict(max_documents=1), dict(max_archive_bytes=1000), dict(max_fields=7),
                      dict(max_json_depth=3)):
            with self.subTest(bound=bound):
                with self.assertRaises(HostileReport): self.read(limits=bound)
        self.assertEqual(len(self.read(limits=dict(max_rows=9)).records), 9)
        for bound in (dict(max_rows=0), dict(max_rows=10**9), dict(unknown=1)):
            with self.subTest(bound=bound):
                with self.assertRaisesRegex(ValueError, 'source limits'): self.read(limits=bound)

    def test_archive_wide_bounds_stop_decoding_before_the_last_file(self):
        # Records are counted per decoded file: the first document already exceeds the bound, so no later file is decoded.
        decoded, actual = [], hindsight.load_json
        def load(raw, *args, **kwargs): decoded.append(raw); return actual(raw, *args, **kwargs)
        with patch.object(hindsight, 'load_json', load), self.assertRaisesRegex(HostileReport, 'row bound'):
            self.read(limits=dict(max_rows=2))
        self.assertEqual(len(decoded), 2)
        # Every file stays inside the per-file JSON caps, but together they exceed them; decoded trees are all held.
        wide = {f'documents/{i:06d}.json': dict(id=f'doc-{i}', facts=[dict(metadata=[{}]*40, tags=[0]*60)]) for i in range(3)}
        wide['manifest.json'] = dict(MANIFEST, document_count=3, observation_count=0, invalidated_memory_count=0)
        self.assertEqual(len(self.read(wide).records), 7)
        for bound in (dict(max_json_containers=100), dict(max_json_values=150)):
            with self.subTest(bound=bound):
                with self.assertRaisesRegex(HostileReport, 'container bound|value bound'): self.read(wide, limits=bound)

    def test_capture_changes_and_unsafe_archive_paths_are_refused(self):
        with patch.object(hindsight, 'regular_sha256', lambda path, limit: '0'*64):
            with self.assertRaisesRegex(ValueError, 'changed during capture'): self.read()
        for archive in ('../bank-a', '/etc', 'exports/bank-a/'):
            with self.subTest(archive=archive):
                with self.assertRaisesRegex(ValueError, 'unsafe relative path'): self.read(archive=archive)
        (self.root/'exports/link').symlink_to(self.root/'exports/bank-a')
        with self.assertRaisesRegex(ValueError, 'symlink'): self.read(archive='exports/link')

    def test_cli_preview_import_reimport_and_correction_keep_hashes_only_and_no_network(self):
        with patch.object(socket, 'socket', side_effect=AssertionError('network')):
            code, preview = self.invoke('preview')
            self.assertEqual(code, 0)
            result = json.loads(preview)
            self.assertEqual((result['records'], result['hindsight_summary']['profile']), (9, 'hindsight-bank-archive-v1'))
            self.assertFalse((self.root/'private').exists())
            code, receipt = self.invoke('import')
        self.assertEqual((code, json.loads(receipt)['imported']), (0, 9))
        raw = (self.root/'private/cache.json').read_text()
        for leak in LEAKS: self.assertNotIn(leak, raw)
        self.assertEqual(json.loads(self.invoke('import')[1])['skipped'], 9)
        # A user correction moves a live fact into the curation archive and retains a replacement: the invalidated
        # fact, the replacement and its relation are new claims; every earlier claim is left as it was.
        alpha = copy.deepcopy(ALPHA)
        alpha['facts'][1].update(source_id='unit-4', text='invented office moved to Braga')
        retracted = dict(INVALIDATED[0], id='unit-2', invalidated_at='2031-04-30T00:00:00Z')
        self.write(dict(PARTS, **{'documents/000000.json': alpha, 'manifest.json': dict(MANIFEST, invalidated_memory_count=2),
                                  'data/invalidated_memory_units.json': [*INVALIDATED, retracted]}))
        receipt = json.loads(self.invoke('import')[1])
        self.assertEqual((receipt['imported'], receipt['skipped']), (4, 6))
        self.connector.read(self.source)
        self.assertEqual(self.connector.summary['assertions']['invalidated'], 2)
        self.assertNotIn('invalidated_fact_still_live', self.connector.summary['warnings'])
        before = (self.root/'private/cache.json').read_bytes()
        self.write(dict(PARTS, **{'observations.json': '['}))
        self.assertEqual(self.invoke('import')[0], 2)
        self.assertEqual((self.root/'private/cache.json').read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
