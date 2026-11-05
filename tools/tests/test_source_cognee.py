"""Synthetic Cognee v1.6.5 graph exports; dataset-scoped derived claims, never recall, facts or task outcomes."""
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.sources import cognee
from agent_harness_lab.sources.cognee import CogneeExport, GraphClaim
from agent_harness_lab.sources.hardened import HostileReport
from agent_harness_lab.sources.schema import SourceSpec, digest

SECRET = 'SYNTHETIC_SECRET_TOKEN'
DATASET_A, DATASET_B = '0f0e0d0c-0b0a-4908-8706-050403020100', '1a1b1c1d-1e1f-4021-8223-242526272829'
DOC, CHUNK, ALICE, ACME, PERSON = 'node-doc-1', 'node-chunk-1', 'node-alice', 'node-acme', 'node-person'
CREATED = 1933135628000


def node(identity, kind, **fields):
    return dict(id=identity, type=kind, created_at=CREATED, updated_at=CREATED, version=1, **fields)


NODES = [
    node(DOC, 'TextDocument', name='invented.txt', raw_data_location='/invented/host/'+SECRET, mime_type='text/plain'),
    node(CHUNK, 'DocumentChunk', text='Invented Alice works at Acme '+SECRET, chunk_size=6, chunk_index=0, cut_type='paragraph',
         document_id=DOC, document_name='invented.txt'),
    node(ALICE, 'Entity', name='Invented Alice', description='an invented person'),
    node(ACME, 'Entity', name='Invented Acme', description='an invented company'),
    node(PERSON, 'EntityType', name='person', description='invented type')]
EDGES = [(CHUNK, DOC, 'is_part_of', {}), (CHUNK, ALICE, 'contains', {}), (CHUNK, ACME, 'contains', {}),
         (ALICE, PERSON, 'is_a', {}), (ALICE, ACME, 'works_at', dict(edge_text='invented fact '+SECRET, weight=0.9))]


def snapshot(dataset_id=DATASET_A, nodes=NODES, edges=EDGES):
    """GraphSnapshot.save(): dataset fields, nodes, and edges with a nested properties bag."""
    return dict(dataset_name='invented-dataset', dataset_id=dataset_id, nodes=copy.deepcopy(nodes),
                edges=[dict(source_id=s, target_id=t, relationship=r, properties=dict(p)) for s, t, r, p in edges])


def plain(nodes=NODES, edges=EDGES):
    """migration/formats.write_json: no dataset identity, edge properties flattened beside the endpoints."""
    return dict(nodes=copy.deepcopy(nodes), edges=[dict(source=s, target=t, relationship_name=r, **p) for s, t, r, p in edges])


class CogneeSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'exports').mkdir()
        self.write(snapshot())

    def write(self, value, raw=None, **selection):
        (self.root/'exports/cognee.json').write_text(raw if raw is not None else json.dumps(value))
        self.selection = dict(dict(schema_version=1, cognee_version='1.6.5', instance='fixture-instance', dataset='dataset-a',
            dataset_id=DATASET_A, format='graph_snapshot', export='exports/cognee.json'), **selection)
        (self.root/'selection.json').write_text(json.dumps(self.selection))
        self.source = SourceSpec('local_file', 'cognee-export', 'v1', digest(str(self.root.resolve())),
            digest('synthetic-project'), digest(self.selection), identity_provenance='source_declared_id', enabled=True)
        self.manifest = self.root/'source.json'
        self.manifest.write_text(json.dumps(dict(schema_version=1, source=self.source.to_dict(), data='selection.json')))
        self.connector = CogneeExport(self.manifest)

    def read(self, value=None, **selection):
        if value is not None or selection: self.write(snapshot() if value is None else value, **selection)
        return self.connector.read(self.source)

    def claims(self, kind):
        return [c for c in self.connector.claims if c.claim_type == kind]

    def invoke(self, operation):
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            code = main(['sources', operation, '--manifest', str(self.manifest), '--project-sha256', self.source.project_sha256,
                '--scope-sha256', self.source.scope_sha256, '--cache', str(self.root/'private/cache.json')])
        for text in ('SYNTHETIC_SECRET', 'Invented', 'invented', 'node-', DATASET_A, 'works_at'):
            self.assertNotIn(text, output.getvalue()+error.getvalue())
        return code, output.getvalue()

    def test_documents_chunks_entities_and_edges_are_separate_derived_claims(self):
        result = self.read()
        summary = self.connector.summary
        self.assertEqual(summary['claims'], dict(document=1, chunk=1, entity=3, graph_node=0, relation=5))
        self.assertEqual(len(result.records), 11)
        self.assertEqual((summary['dataset_lineage'], summary['cross_source_joins']), ('export_matches_declaration', 'dataset_id_bound'))
        # The clean fixture is the baseline every warning test is compared against.
        self.assertEqual(summary['warnings'], ['edges_not_independent', 'entities_model_extracted', 'entity_origin_untraced',
                                               'retrieval_scores_not_in_export', 'sessions_not_in_export'])
        provenance = {r.native_id_sha256: (r.record_type, r.provenance) for r in result.records}
        details = {c.detail: provenance[c.native_id_sha256] for c in self.claims('relation')}
        self.assertEqual(details, dict(is_part_of=('cognee_relation', 'native_field'), contains=('cognee_relation', 'native_field'),
                                       is_a=('cognee_relation', 'native_field'), other=('cognee_relation', 'model_hypothesis')))
        self.assertEqual({provenance[c.native_id_sha256] for c in self.claims('entity')}, {('cognee_entity', 'model_hypothesis')})
        for record in result.records:
            self.assertEqual((record.plane, record.causal_status, record.independence, record.capture_completeness),
                             ('contextual_assertion', 'unknown', 'unknown', 'partial'))
        for claim in self.connector.claims:
            self.assertEqual((claim.independent_case, claim.factually_verified, claim.task_outcome_verified), (False,)*3)
        relation = self.claims('relation')[-1]
        for change in (dict(factually_verified=True), dict(claim_type='entity'), dict(source_node_sha256=None)):
            with self.subTest(change=change), self.assertRaises(ValueError): GraphClaim(**dict(relation.to_dict(), **change))
        for key in ('recall_used', 'inference_used', 'graph_mutated', 'network_used', 'database_opened', 'bodies_stored',
                    'task_outcomes_verified', 'publication_allowed'):
            self.assertIs(summary[key], False)
        self.assertEqual((summary['profile'], summary['upstream_commit'], summary['compatibility'], summary['version_attestation']),
                         ('cognee-export-v1', '37f788bd287750e10d7610173e5332b3843f6ebd', 'unverified', 'caller_declared'))
        self.assertFalse(public_export_allowed([dict(summary, publication_allowed=True,
            provenance=dict(source_kind='synthetic', approved_publication=True, source_ref='x', derived_from_private=False))]))

    def test_derived_claims_trace_back_to_their_origin_document(self):
        self.read()
        document, = self.claims('document')
        chunk, = self.claims('chunk')
        entities_before = {n['id']: c.native_id_sha256 for n, c in zip(NODES, self.connector.claims)}
        self.assertEqual((document.origin_status, document.origin_document_sha256), ('self', None))
        self.assertEqual(chunk.origin_status, 'traced')
        origin = chunk.origin_document_sha256
        # Entities named by the chunk's contains edges trace to its document; the type node has no source chunk.
        self.assertEqual(sorted((c.origin_status, c.origin_document_sha256 == origin) for c in self.claims('entity')),
                         [('traced', True), ('traced', True), ('untraced', False)])
        edges = {c.detail: c for c in self.claims('relation')}
        self.assertEqual((edges['is_part_of'].origin_status, edges['is_part_of'].origin_document_sha256), ('traced', origin))
        self.assertEqual((edges['other'].origin_status, edges['other'].origin_document_sha256), ('traced', origin))
        self.assertEqual(edges['is_part_of'].target_node_sha256, origin)
        # Every origin and endpoint hash resolves to the node claim it names.
        ids = {n['id']: c.native_id_sha256 for n, c in zip(NODES, self.connector.claims)}
        self.assertEqual(origin, document.native_id_sha256)
        self.assertEqual({c.origin_document_sha256 for c in self.claims('entity') if c.origin_status == 'traced'}, {ids[DOC]})
        self.assertEqual([(c.source_node_sha256, c.target_node_sha256) for c in self.claims('relation')],
                         [(ids[s], ids[t]) for s, t, r, p in EDGES])
        # An entity named by chunks of two documents has several origins and names none of them.
        second = [node('node-doc-2', 'PdfDocument', name='other.pdf'), dict(NODES[1], id='node-chunk-2', document_id='node-doc-2')]
        self.read(snapshot(nodes=NODES+second, edges=EDGES+[('node-chunk-2', ALICE, 'contains', {})]))
        alice = next(c for c in self.claims('entity') if c.native_id_sha256 == entities_before[ALICE])
        self.assertEqual((alice.origin_status, alice.origin_document_sha256), ('multiple', None))
        self.assertEqual(next(c for c in self.claims('relation') if c.detail == 'other').origin_status, 'multiple')

    def test_datasets_a_and_b_cannot_be_mixed(self):
        a = self.read()
        a_ids, a_dataset = {r.native_id_sha256 for r in a.records}, self.connector.summary['dataset_sha256']
        # Dataset B's export under A's selection is refused; nothing from it is admitted.
        with self.assertRaisesRegex(ValueError, 'undeclared dataset'): self.read(snapshot(DATASET_B))
        with self.assertRaisesRegex(ValueError, 'undeclared dataset'): self.read(snapshot(DATASET_B), dataset_id=None)
        # The same Cognee node IDs under B's own selection are B's claims, never A's.
        b = self.read(snapshot(DATASET_B), dataset='dataset-b', dataset_id=DATASET_B)
        self.assertFalse(a_ids & {r.native_id_sha256 for r in b.records})
        self.assertNotEqual(self.connector.summary['dataset_sha256'], a_dataset)
        # Two verified datasets sharing one label (Cognee's default name) still never share a claim or a dataset hash.
        same = self.read(snapshot(DATASET_B), dataset='dataset-a', dataset_id=DATASET_B)
        self.assertEqual(self.connector.summary['cross_source_joins'], 'dataset_id_bound')
        self.assertFalse(a_ids & {r.native_id_sha256 for r in same.records})
        self.assertNotEqual(self.connector.summary['dataset_sha256'], a_dataset)
        for dataset_id in ('not-a-uuid', DATASET_A.upper(), 7):
            with self.subTest(dataset_id=dataset_id), self.assertRaisesRegex(ValueError, 'canonical UUID'):
                self.read(snapshot(), dataset_id=dataset_id)

    def test_missing_dataset_identity_is_unknown_lineage_with_joins_disabled(self):
        for value, selection in ((plain(), dict(format='nodes_edges')), (plain(), dict(format='nodes_edges', dataset_id=None)),
                                 (snapshot(''), {})):
            with self.subTest(selection=selection):
                self.read(value, **selection)
                summary = self.connector.summary
                expected = 'unknown' if selection.get('dataset_id', DATASET_A) is None else 'caller_declared'
                self.assertEqual((summary['dataset_lineage'], summary['cross_source_joins']), (expected, 'disabled'))
                self.assertIn('dataset_lineage_unverified', summary['warnings'])
                self.assertEqual(summary['claims']['relation'], 5)
        with self.assertRaisesRegex(ValueError, 'undeclared dataset'): self.read(snapshot(), dataset_id=None)

    def test_structural_names_count_only_between_their_pinned_types(self):
        # A model-extracted relation that happens to reuse a structural name is still a model hypothesis.
        misnamed = [(ALICE, ACME, 'contains', {}), (ACME, ALICE, 'is_a', {}), (ALICE, DOC, 'is_part_of', {}),
                    (CHUNK, PERSON, 'contains', {}), (CHUNK, 'node-missing', 'is_part_of', {})]
        result = self.read(snapshot(edges=EDGES+misnamed))
        provenance = {r.native_id_sha256: r.provenance for r in result.records}
        relations = self.claims('relation')
        self.assertEqual([(c.detail, provenance[c.native_id_sha256]) for c in relations[len(EDGES):]],
                         [('other', 'model_hypothesis')]*len(misnamed))
        self.assertEqual([c.detail for c in relations[:len(EDGES)]], ['is_part_of', 'contains', 'contains', 'is_a', 'other'])
        # Misnamed edges never decide an origin: the chunk keeps its one document and the contained type stays untraced.
        ids = {n['id']: c.native_id_sha256 for n, c in zip(NODES, self.connector.claims)}
        origins = {c.native_id_sha256: (c.origin_status, c.origin_document_sha256) for c in self.connector.claims}
        self.assertEqual(origins[ids[CHUNK]], ('traced', ids[DOC]))
        self.assertEqual([origins[ids[i]] for i in (ALICE, ACME, PERSON)], [('traced', ids[DOC])]*2 + [('untraced', None)])
        self.assertEqual([(c.origin_status, c.origin_document_sha256) for c in relations[len(EDGES):]],
                         [('traced', ids[DOC])]*4 + [('missing', None)])
        self.assertFalse({'chunk_origin_conflicting', 'entity_origin_traced'} & set(self.connector.summary['warnings']))

    def test_origins_never_name_an_exported_non_document(self):
        ids = lambda: {n['id']: c.native_id_sha256 for n, c in zip(nodes, self.connector.claims)}
        # A document_id, or the only is_part_of edge, naming an exported entity names no document.
        bare = {k: v for k, v in NODES[1].items() if k != 'document_id'}
        for case, chunk, edges in (('document_id', dict(NODES[1], document_id=ALICE), EDGES[1:]),
                                   ('is_part_of', bare, [(CHUNK, ALICE, 'is_part_of', {})]+EDGES[1:])):
            with self.subTest(case=case):
                nodes = [NODES[0], chunk, *NODES[2:]]
                self.read(snapshot(nodes=nodes, edges=edges))
                chunk_claim, = self.claims('chunk')
                self.assertEqual((chunk_claim.origin_status, chunk_claim.origin_document_sha256), ('missing', None))
                self.assertNotIn(ids()[ALICE], {c.origin_document_sha256 for c in self.connector.claims})
                self.assertEqual(self.connector.summary['origins']['not_exported'], 0)
                self.assertIn('chunk_origin_missing', self.connector.summary['warnings'])
                self.assertNotIn('chunk_origin_not_exported', self.connector.summary['warnings'])
        # Beside a document_id naming an exported document, an is_part_of edge to an entity changes nothing.
        nodes = NODES
        self.read(snapshot(edges=EDGES+[(CHUNK, ALICE, 'is_part_of', {})]))
        chunk_claim, = self.claims('chunk')
        self.assertEqual((chunk_claim.origin_status, chunk_claim.origin_document_sha256), ('traced', ids()[DOC]))

    def test_missing_refs_altered_edges_and_revisions_are_explicit(self):
        nodes = [n for n in NODES if n['id'] != DOC] + [dict(NODES[1], id='node-chunk-2', document_id='node-elsewhere')]
        nodes.append(dict(id=None, type='Entity'))
        edges = EDGES + [(ALICE, 'node-missing', 'works_at', {}), (CHUNK, None, 'contains', {})]
        self.read(snapshot(nodes=nodes, edges=edges))
        summary = self.connector.summary
        self.assertEqual(summary['rows_rejected'], dict(nodes=1, edges=1))
        self.assertTrue({'node_without_native_identity', 'edge_without_endpoints', 'edge_endpoint_not_exported',
                         'chunk_origin_not_exported'} <= set(summary['warnings']))
        self.assertEqual({c.origin_status for c in self.claims('chunk')}, {'not_exported'})
        # A chunk whose document_id and is_part_of edge disagree is conflicting, never resolved by choice.
        self.read(snapshot(nodes=[dict(NODES[1], document_id='node-other'), *NODES[:1], *NODES[2:]]))
        self.assertEqual(self.claims('chunk')[0].origin_status, 'conflicting')
        self.assertIn('chunk_origin_conflicting', self.connector.summary['warnings'])
        # Edited edge properties and a bumped node version are new revisions of the same native claims.
        self.read(snapshot())
        before = {c.native_id_sha256: digest(c.to_dict()) for c in self.connector.claims}
        changed = [dict(n, version=2) if n['id'] == ALICE else n for n in NODES]
        self.read(snapshot(nodes=changed, edges=EDGES[:-1]+[(ALICE, ACME, 'works_at', dict(weight=0.1))]))
        after = {c.native_id_sha256: digest(c.to_dict()) for c in self.connector.claims}
        self.assertEqual(set(before), set(after))
        self.assertEqual(sum(before[k] != after[k] for k in before), 2)

    def test_versions_formats_and_envelopes_fail_closed(self):
        for change in (dict(cognee_version='1.6.4'), dict(cognee_version=None), dict(format='cogx'), dict(format='graphml'),
                       dict(format='cypher'), dict(format='nodes_edges')):
            with self.subTest(change=change), self.assertRaises(ValueError): self.read(snapshot(), **change)
        for value in (plain(), dict(snapshot(), cognee_version='1.6.6'), dict(snapshot(), edges={}), [], dict(snapshot(), nodes=['x'])):
            with self.subTest(value=str(value)[:40]), self.assertRaises(ValueError): self.read(value)
        self.read(dict(snapshot(), cognee_version='1.6.5', extra=1))
        self.assertEqual(self.connector.summary['version_attestation'], 'export_declared')
        self.assertIn('unknown_envelope_fields', self.connector.summary['warnings'])
        self.write(None, raw='{"nodes": [], "nodes": [], "edges": [], "dataset_id": ""}')
        with self.assertRaises(ValueError): self.connector.read(self.source)

    def test_bounds_abort_before_mapping(self):
        many = snapshot(nodes=[node(f'node-{i}', 'Entity') for i in range(20)], edges=[])
        cases = ((snapshot(), dict(max_export_bytes=100)), (many, dict(max_rows=20)),
                 (snapshot(nodes=[dict(NODES[0], **{f'x{i}': i for i in range(20)})]), dict(max_fields=16)),
                 (snapshot(), dict(max_json_containers=5)))
        for value, bound in cases:
            with self.subTest(bound=bound), self.assertRaises(ValueError): self.read(value, limits=bound)
        self.assertEqual(len(self.read(many, limits=dict(max_rows=21)).records), 21)
        with self.assertRaisesRegex(ValueError, 'only lower'): self.read(snapshot(), limits=dict(max_rows=10**9))
        # A plain-JSON edge carries its properties inline, so they count once toward max_fields.
        wide = plain(nodes=NODES[2:4], edges=[(ALICE, ACME, 'works_at', {f'p{i}': i for i in range(5)})])
        self.assertEqual(len(self.read(wide, format='nodes_edges', limits=dict(max_fields=8)).records), 4)
        with self.assertRaisesRegex(HostileReport, 'field bound'): self.read(wide, format='nodes_edges', limits=dict(max_fields=7))
        self.write(snapshot(), limits=dict(max_json_containers=5))
        with patch.object(cognee, 'parse', side_effect=AssertionError('decoded')), self.assertRaises(HostileReport):
            self.connector.read(self.source)

    def test_capture_change_is_refused(self):
        with patch.object(cognee, 'regular_sha256', lambda path, limit: '0'*64):
            with self.assertRaisesRegex(ValueError, 'changed during capture'): self.connector.read(self.source)

    def test_cli_import_holds_hashes_only_and_never_loads_cognee(self):
        self.invoke('status')
        # Cognee is made unimportable; the CLI's own lazy imports are loaded above so the patch does not unload them.
        with patch.dict(sys.modules, {'cognee': None}):
            code, preview = self.invoke('preview')
            self.assertEqual((code, json.loads(preview)['cognee_summary']['profile']), (0, 'cognee-export-v1'))
            code, receipt = self.invoke('import')
        self.assertEqual((code, json.loads(receipt)['imported']), (0, 11))
        raw = (self.root/'private/cache.json').read_text()
        for text in (SECRET, 'Invented', 'node-', DATASET_A, 'works_at', 'exports/cognee.json'):
            self.assertNotIn(text, raw)
        self.assertEqual(json.loads(self.invoke('import')[1])['skipped'], 11)
        before = (self.root/'private/cache.json').read_bytes()
        (self.root/'exports/cognee.json').write_text(json.dumps(snapshot(DATASET_B)))
        self.assertEqual(self.invoke('import')[0], 2)
        self.assertEqual((self.root/'private/cache.json').read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
