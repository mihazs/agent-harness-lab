"""Synthetic Cognee v1.6.5 graph exports; dataset-scoped derived claims, never recall, facts or task outcomes."""
import contextlib
import copy
import io
import json
import shutil
import sys
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

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

CREATED_ISO = datetime.fromtimestamp(CREATED/1000, timezone.utc).isoformat()
ARCHIVE_NAME = 'exports/cogx'


def cogx(nodes=NODES, edges=EDGES, **manifest):
    """_write_cogx at the pin: a named Entity is a COGXEntity, a chunk a COGXDocument and a raw node, any other node a raw node."""
    files, counts = {}, {}
    def add(name, kind, row):
        files.setdefault(name, []).append(row); counts[kind] = counts.get(kind, 0)+1
    for item in nodes:
        fields = {k: v for k, v in item.items() if k != 'id'}
        if item['type'] == 'Entity' and item.get('name'):
            add('entities.jsonl', 'entity', dict(kind='entity', external_system='cognee', external_id=item['id'], name=item['name'],
                description=item.get('description'), created_at=CREATED_ISO, updated_at=CREATED_ISO))
        else:
            if item['type'] == 'DocumentChunk':
                add('documents.jsonl', 'document', dict(kind='document', external_system='cognee', external_id=item['id'],
                    content=item['text'], created_at=CREATED_ISO))
            add('nodes.jsonl', 'raw_node', dict(id=item['id'], **fields))
    for source, target, name, properties in edges:
        add('facts.jsonl', 'fact', dict(kind='fact', external_system='cognee', external_id=f'{source}:{name}:{target}',
            subject_ref=source, predicate=name, object_ref=target, **({'fact_text': properties['edge_text']} if 'edge_text' in properties else {})))
    files['manifest.json'] = dict(dict(cogx_version='0.1', source_system='cognee', exported_at='2031-05-01T10:00:00Z', counts=counts,
        notes=["Exported from Cognee dataset 'invented-dataset'."]), **manifest)
    return files

SESSION = 'invented-session-1'
QA = dict(source='session', time='2031-05-01T10:00:00+00:00', qa_id='qa-1', question='invented question '+SECRET,
          context='invented context', answer='invented answer', used_graph_element_ids=dict(node_ids=[ALICE], edge_ids=['edge-1']))
HIT = dict(source='graph', kind='chunk', search_type='CHUNKS', text='invented chunk '+SECRET, score=0.42, dataset_id=DATASET_A)
ANSWER = dict(HIT, kind='graph_completion', search_type='GRAPH_COMPLETION', text='invented generated answer', score=None)
SYMBOL = 'node-symbol-1'
CODE = dict(source='code', kind='code', search_type='CODE', text='invented symbol '+SECRET, dataset_id=DATASET_A,
            raw=dict(id=SYMBOL, kind='symbol', type='CodeSymbol', name='invented_fn', file='/invented/path/'+SECRET, line=3))
CITED = dict(HIT, metadata=dict(data_id=DOC, chunk_id=CHUNK, chunk_index=0, document_name='invented.txt'))
SKILL = dict(source='skills', text='invented: skill', skill=dict(name='invented'), score=0.2)


def graph_hash(claims, detail):
    return next(h for h, c in claims.items() if c.detail == detail)


def recall(*entries):
    """A saved cognee.recall() result list (RecallResponse dumps)."""
    return [copy.deepcopy(e) for e in entries]


MANY = snapshot(nodes=[node(f'node-{i}', 'Entity') for i in range(20)], edges=[])


class TestCogneeSource:
    @pytest.fixture(autouse=True)
    def _export(self, tmp_path):
        self.root = tmp_path
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
            assert text not in output.getvalue()+error.getvalue()
        return code, output.getvalue()

    def test_documents_chunks_entities_and_edges_are_separate_derived_claims(self):
        result = self.read()
        summary = self.connector.summary
        assert summary['claims'] == dict(document=1, chunk=1, entity=3, graph_node=0, relation=5)
        assert len(result.records) == 11
        assert (summary['dataset_lineage'], summary['cross_source_joins']) == ('export_matches_declaration', 'dataset_id_bound')
        # The clean fixture is the baseline every warning test is compared against.
        assert (summary['warnings'] == ['edges_not_independent', 'entities_model_extracted', 'entity_origin_untraced',
                                               'retrieval_scores_not_in_export', 'sessions_not_in_export'])
        provenance = {r.native_id_sha256: (r.record_type, r.provenance) for r in result.records}
        details = {c.detail: provenance[c.native_id_sha256] for c in self.claims('relation')}
        assert (details == dict(is_part_of=('cognee_relation', 'native_field'), contains=('cognee_relation', 'native_field'),
                                       is_a=('cognee_relation', 'native_field'), other=('cognee_relation', 'model_hypothesis')))
        assert {provenance[c.native_id_sha256] for c in self.claims('entity')} == {('cognee_entity', 'model_hypothesis')}
        for record in result.records:
            assert (record.plane, record.causal_status, record.independence, record.capture_completeness) == ('contextual_assertion', 'unknown', 'unknown', 'partial')
        for claim in self.connector.claims:
            assert (claim.independent_case, claim.factually_verified, claim.task_outcome_verified) == (False,)*3
        for key in ('recall_used', 'inference_used', 'graph_mutated', 'network_used', 'database_opened', 'bodies_stored',
                    'task_outcomes_verified', 'publication_allowed'):
            assert summary[key] is False
        assert (summary['profile'], summary['upstream_commit'], summary['compatibility'], summary['version_attestation']) == ('cognee-export-v1', '37f788bd287750e10d7610173e5332b3843f6ebd', 'unverified', 'caller_declared')
        assert (not public_export_allowed([dict(summary, publication_allowed=True,
            provenance=dict(source_kind='synthetic', approved_publication=True, source_ref='x', derived_from_private=False))]))

    @pytest.mark.parametrize('change', [
        pytest.param(dict(factually_verified=True), id='factually_verified'), pytest.param(dict(claim_type='entity'), id='claim_type'),
        pytest.param(dict(source_node_sha256=None), id='source_node')])
    def test_relation_claims_reject_upgrades_and_missing_endpoints(self, change):
        self.read()
        relation = self.claims('relation')[-1]
        with pytest.raises(ValueError): GraphClaim(**dict(relation.to_dict(), **change))

    def test_derived_claims_trace_back_to_their_origin_document(self):
        self.read()
        document, = self.claims('document')
        chunk, = self.claims('chunk')
        entities_before = {n['id']: c.native_id_sha256 for n, c in zip(NODES, self.connector.claims)}
        assert (document.origin_status, document.origin_document_sha256) == ('self', None)
        assert chunk.origin_status == 'traced'
        origin = chunk.origin_document_sha256
        # Entities named by the chunk's contains edges trace to its document; the type node has no source chunk.
        assert sorted((c.origin_status, c.origin_document_sha256 == origin) for c in self.claims('entity')) == [('traced', True), ('traced', True), ('untraced', False)]
        edges = {c.detail: c for c in self.claims('relation')}
        assert (edges['is_part_of'].origin_status, edges['is_part_of'].origin_document_sha256) == ('traced', origin)
        assert (edges['other'].origin_status, edges['other'].origin_document_sha256) == ('traced', origin)
        assert edges['is_part_of'].target_node_sha256 == origin
        # Every origin and endpoint hash resolves to the node claim it names.
        ids = {n['id']: c.native_id_sha256 for n, c in zip(NODES, self.connector.claims)}
        assert origin == document.native_id_sha256
        assert {c.origin_document_sha256 for c in self.claims('entity') if c.origin_status == 'traced'} == {ids[DOC]}
        assert [(c.source_node_sha256, c.target_node_sha256) for c in self.claims('relation')] == [(ids[s], ids[t]) for s, t, r, p in EDGES]
        # An entity named by chunks of two documents has several origins and names none of them.
        second = [node('node-doc-2', 'PdfDocument', name='other.pdf'), dict(NODES[1], id='node-chunk-2', document_id='node-doc-2')]
        self.read(snapshot(nodes=NODES+second, edges=EDGES+[('node-chunk-2', ALICE, 'contains', {})]))
        alice = next(c for c in self.claims('entity') if c.native_id_sha256 == entities_before[ALICE])
        assert (alice.origin_status, alice.origin_document_sha256) == ('multiple', None)
        assert next(c for c in self.claims('relation') if c.detail == 'other').origin_status == 'multiple'

    def test_datasets_a_and_b_cannot_be_mixed(self):
        a = self.read()
        a_ids, a_dataset = {r.native_id_sha256 for r in a.records}, self.connector.summary['dataset_sha256']
        # Dataset B's export under A's selection is refused; nothing from it is admitted.
        with pytest.raises(ValueError, match='undeclared dataset'): self.read(snapshot(DATASET_B))
        with pytest.raises(ValueError, match='undeclared dataset'): self.read(snapshot(DATASET_B), dataset_id=None)
        # The same Cognee node IDs under B's own selection are B's claims, never A's.
        b = self.read(snapshot(DATASET_B), dataset='dataset-b', dataset_id=DATASET_B)
        assert not a_ids & {r.native_id_sha256 for r in b.records}
        assert self.connector.summary['dataset_sha256'] != a_dataset
        # Two verified datasets sharing one label (Cognee's default name) still never share a claim or a dataset hash.
        same = self.read(snapshot(DATASET_B), dataset='dataset-a', dataset_id=DATASET_B)
        assert self.connector.summary['cross_source_joins'] == 'dataset_id_bound'
        assert not a_ids & {r.native_id_sha256 for r in same.records}
        assert self.connector.summary['dataset_sha256'] != a_dataset

    @pytest.mark.parametrize('dataset_id', [pytest.param('not-a-uuid', id='not_a_uuid'), pytest.param(DATASET_A.upper(), id='uppercase'),
                                            pytest.param(7, id='integer')])
    def test_declared_dataset_ids_must_be_canonical_uuids(self, dataset_id):
        with pytest.raises(ValueError, match='canonical UUID'): self.read(snapshot(), dataset_id=dataset_id)

    @pytest.mark.parametrize(('value', 'selection'), [
        pytest.param(plain(), dict(format='nodes_edges'), id='plain_export_declared_id'),
        pytest.param(plain(), dict(format='nodes_edges', dataset_id=None), id='plain_export_no_id'),
        pytest.param(snapshot(''), {}, id='snapshot_empty_id')])
    def test_missing_dataset_identity_is_unknown_lineage_with_joins_disabled(self, value, selection):
        self.read(value, **selection)
        summary = self.connector.summary
        expected = 'unknown' if selection.get('dataset_id', DATASET_A) is None else 'caller_declared'
        assert (summary['dataset_lineage'], summary['cross_source_joins']) == (expected, 'disabled')
        assert 'dataset_lineage_unverified' in summary['warnings']
        assert summary['claims']['relation'] == 5

    def test_undeclared_dataset_identity_is_refused(self):
        with pytest.raises(ValueError, match='undeclared dataset'): self.read(snapshot(), dataset_id=None)

    def test_structural_names_count_only_between_their_pinned_types(self):
        # A model-extracted relation that happens to reuse a structural name is still a model hypothesis.
        misnamed = [(ALICE, ACME, 'contains', {}), (ACME, ALICE, 'is_a', {}), (ALICE, DOC, 'is_part_of', {}),
                    (CHUNK, PERSON, 'contains', {})]
        result = self.read(snapshot(edges=EDGES+misnamed))
        provenance = {r.native_id_sha256: r.provenance for r in result.records}
        relations = self.claims('relation')
        assert [(c.detail, provenance[c.native_id_sha256]) for c in relations[len(EDGES):]] == [('other', 'model_hypothesis')]*len(misnamed)
        assert [c.detail for c in relations[:len(EDGES)]] == ['is_part_of', 'contains', 'contains', 'is_a', 'other']
        # Misnamed edges never decide an origin: the chunk keeps its one document and the contained type stays untraced.
        ids = {n['id']: c.native_id_sha256 for n, c in zip(NODES, self.connector.claims)}
        origins = {c.native_id_sha256: (c.origin_status, c.origin_document_sha256) for c in self.connector.claims}
        assert origins[ids[CHUNK]] == ('traced', ids[DOC])
        assert [origins[ids[i]] for i in (ALICE, ACME, PERSON)] == [('traced', ids[DOC])]*2 + [('untraced', None)]
        assert [(c.origin_status, c.origin_document_sha256) for c in relations[len(EDGES):]] == [('traced', ids[DOC])]*4
        assert not {'chunk_origin_conflicting', 'entity_origin_traced'} & set(self.connector.summary['warnings'])

    def test_is_part_of_to_an_unexported_node_still_names_a_candidate_document(self):
        # Beside a document_id naming an exported document, an is_part_of edge to an absent node is a disagreement.
        self.read(snapshot(edges=EDGES+[(CHUNK, 'node-gone', 'is_part_of', {})]))
        chunk_claim, = self.claims('chunk')
        assert (chunk_claim.origin_status, chunk_claim.origin_document_sha256) == ('conflicting', None)
        assert [c.origin_status for c in self.claims('entity')] == ['untraced']*3
        assert 'chunk_origin_conflicting' in self.connector.summary['warnings']
        assert 'entity_origin_traced' not in self.connector.summary['warnings']
        # Its name is still not structural, as the target's type is not exported.
        assert self.claims('relation')[-1].detail == 'other'
        # The mirror case: a document_id naming an absent node beside an is_part_of edge to the exported document.
        nodes = [NODES[0], dict(NODES[1], document_id='node-gone'), *NODES[2:]]
        self.read(snapshot(nodes=nodes))
        chunk_claim, = self.claims('chunk')
        assert (chunk_claim.origin_status, chunk_claim.origin_document_sha256) == ('conflicting', None)
        # A chunk whose only is_part_of edge names an absent node names a document that is not exported.
        nodes = [NODES[0], {k: v for k, v in NODES[1].items() if k != 'document_id'}, *NODES[2:]]
        self.read(snapshot(nodes=nodes, edges=[(CHUNK, 'node-gone', 'is_part_of', {})]+EDGES[1:]))
        chunk_claim, = self.claims('chunk')
        assert chunk_claim.origin_status == 'not_exported'
        assert chunk_claim.origin_document_sha256 not in {c.native_id_sha256 for c in self.connector.claims}
        assert 'chunk_origin_not_exported' in self.connector.summary['warnings']

    @pytest.mark.parametrize('case', ['document_id', 'is_part_of'])
    def test_origins_never_name_an_exported_non_document(self, case):
        # A document_id, or the only is_part_of edge, naming an exported entity names no document.
        bare = {k: v for k, v in NODES[1].items() if k != 'document_id'}
        chunk, edges = (dict(NODES[1], document_id=ALICE), EDGES[1:]) if case == 'document_id' else (bare, [(CHUNK, ALICE, 'is_part_of', {})]+EDGES[1:])
        nodes = [NODES[0], chunk, *NODES[2:]]
        self.read(snapshot(nodes=nodes, edges=edges))
        chunk_claim, = self.claims('chunk')
        assert (chunk_claim.origin_status, chunk_claim.origin_document_sha256) == ('missing', None)
        ids = {n['id']: c.native_id_sha256 for n, c in zip(nodes, self.connector.claims)}
        assert ids[ALICE] not in {c.origin_document_sha256 for c in self.connector.claims}
        assert self.connector.summary['origins']['not_exported'] == 0
        assert 'chunk_origin_missing' in self.connector.summary['warnings']
        assert 'chunk_origin_not_exported' not in self.connector.summary['warnings']

    def test_an_is_part_of_edge_to_an_entity_beside_an_exported_document_id_changes_nothing(self):
        self.read(snapshot(edges=EDGES+[(CHUNK, ALICE, 'is_part_of', {})]))
        chunk_claim, = self.claims('chunk')
        ids = {n['id']: c.native_id_sha256 for n, c in zip(NODES, self.connector.claims)}
        assert (chunk_claim.origin_status, chunk_claim.origin_document_sha256) == ('traced', ids[DOC])

    def test_missing_refs_altered_edges_and_revisions_are_explicit(self):
        nodes = [n for n in NODES if n['id'] != DOC] + [dict(NODES[1], id='node-chunk-2', document_id='node-elsewhere')]
        nodes.append(dict(id=None, type='Entity'))
        edges = EDGES + [(ALICE, 'node-missing', 'works_at', {}), (CHUNK, None, 'contains', {})]
        self.read(snapshot(nodes=nodes, edges=edges))
        summary = self.connector.summary
        assert summary['rows_rejected'] == dict(nodes=1, edges=1)
        assert ({'node_without_native_identity', 'edge_without_endpoints', 'edge_endpoint_not_exported',
                         'chunk_origin_not_exported'} <= set(summary['warnings']))
        assert {c.origin_status for c in self.claims('chunk')} == {'not_exported'}
        # A chunk whose document_id and is_part_of edge disagree is conflicting, never resolved by choice.
        self.read(snapshot(nodes=[dict(NODES[1], document_id='node-other'), *NODES[:1], *NODES[2:]]))
        assert self.claims('chunk')[0].origin_status == 'conflicting'
        assert 'chunk_origin_conflicting' in self.connector.summary['warnings']
        # Edited edge properties and a bumped node version are new revisions of the same native claims.
        self.read(snapshot())
        before = {c.native_id_sha256: digest(c.to_dict()) for c in self.connector.claims}
        changed = [dict(n, version=2) if n['id'] == ALICE else n for n in NODES]
        self.read(snapshot(nodes=changed, edges=EDGES[:-1]+[(ALICE, ACME, 'works_at', dict(weight=0.1))]))
        after = {c.native_id_sha256: digest(c.to_dict()) for c in self.connector.claims}
        assert set(before) == set(after)
        assert sum(before[k] != after[k] for k in before) == 2

    @pytest.mark.parametrize('change', [
        pytest.param(dict(cognee_version='1.6.4'), id='older_version'), pytest.param(dict(cognee_version=None), id='no_version'),
        pytest.param(dict(format='cogx'), id='cogx'), pytest.param(dict(format='graphml'), id='graphml'),
        pytest.param(dict(format='cypher'), id='cypher'), pytest.param(dict(format='nodes_edges'), id='nodes_edges_for_snapshot')])
    def test_unsupported_selection_versions_and_formats_are_refused(self, change):
        with pytest.raises(ValueError): self.read(snapshot(), **change)

    @pytest.mark.parametrize('value', [
        pytest.param(plain(), id='plain_export'), pytest.param(dict(snapshot(), cognee_version='1.6.6'), id='newer_export_version'),
        pytest.param(dict(snapshot(), edges={}), id='edges_object'), pytest.param([], id='array'),
        pytest.param(dict(snapshot(), nodes=['x']), id='node_row_string')])
    def test_unsupported_export_envelopes_are_refused(self, value):
        with pytest.raises(ValueError): self.read(value)

    def test_versions_formats_and_envelopes_fail_closed(self):
        self.read(dict(snapshot(), cognee_version='1.6.5', extra=1))
        assert self.connector.summary['version_attestation'] == 'export_declared'
        assert 'unknown_envelope_fields' in self.connector.summary['warnings']
        self.write(None, raw='{"nodes": [], "nodes": [], "edges": [], "dataset_id": ""}')
        with pytest.raises(ValueError): self.connector.read(self.source)

    @pytest.mark.parametrize(('value', 'bound'), [
        pytest.param(snapshot(), dict(max_export_bytes=100), id='export_bytes'), pytest.param(MANY, dict(max_rows=20), id='rows'),
        pytest.param(snapshot(nodes=[dict(NODES[0], **{f'x{i}': i for i in range(20)})]), dict(max_fields=16), id='fields'),
        pytest.param(snapshot(), dict(max_json_containers=5), id='containers')])
    def test_bounds_abort_before_mapping_refuse_the_export(self, value, bound):
        with pytest.raises(ValueError): self.read(value, limits=bound)

    def test_bounds_abort_before_mapping(self):
        assert len(self.read(MANY, limits=dict(max_rows=21)).records) == 21
        with pytest.raises(ValueError, match='only lower'): self.read(snapshot(), limits=dict(max_rows=10**9))
        # A plain-JSON edge carries its properties inline, so they count once toward max_fields.
        wide = plain(nodes=NODES[2:4], edges=[(ALICE, ACME, 'works_at', {f'p{i}': i for i in range(5)})])
        assert len(self.read(wide, format='nodes_edges', limits=dict(max_fields=8)).records) == 4
        with pytest.raises(HostileReport, match='field bound'): self.read(wide, format='nodes_edges', limits=dict(max_fields=7))
        self.write(snapshot(), limits=dict(max_json_containers=5))
        with patch.object(cognee, 'parse', side_effect=AssertionError('decoded')), pytest.raises(HostileReport):
            self.connector.read(self.source)

    def read_recall(self, entries, **selection):
        return self.read(entries, **dict(dict(format='recall_results', session_id=SESSION), **selection))

    def test_saved_recall_results_keep_source_session_score_and_origin_links_apart(self):
        self.read()
        graph_ids = {c.native_id_sha256 for c in self.connector.claims if c.detail == 'Entity'}
        result = self.read_recall(recall(QA, HIT, ANSWER, SKILL, dict(source='trace', trace_id='t-1', origin_function='f', status='ok')))
        summary, claims = self.connector.summary, {c.claim_type: c for c in self.connector.claims}
        assert summary['claims']['graph'] == 2 and len(result.records) == 6 and len(claims) == 4
        qa = claims['recall_session']
        assert (qa.session_status, qa.timestamp_status, qa.graph_refs_status, qa.derivation) == ('declared', 'native', 'named', 'session_qa_entry')
        # The cited node hashes to the claim a graph export of the same dataset carries for that node.
        assert set(qa.graph_node_refs) <= graph_ids and len(qa.graph_node_refs) == 1
        hits = [c for c in self.connector.claims if c.claim_type == 'recall_graph']
        assert {(c.derivation, c.score, c.score_semantics) for c in hits} == {('retrieved_item', 0.42, 'unspecified'), ('generated_answer', None, 'none')}
        assert (claims['recall_skills'].score_semantics, claims['recall_trace'].session_status) == ('vector_distance_lower_is_better', 'declared')
        assert {'retrieval_scores_not_correctness', 'answers_not_graded_outcomes'} <= set(summary['warnings'])
        assert (summary['cross_source_joins'], summary['recall_used'], summary['inference_used'], summary['network_used']) == ('disabled', False, False, False)
        assert all((c.independent_case, c.factually_verified, c.task_outcome_verified) == (False,)*3 for c in self.connector.claims)

    def test_recall_datasets_a_and_b_cannot_be_mixed_and_missing_ids_are_explicit(self):
        with pytest.raises(ValueError, match='undeclared dataset'): self.read_recall(recall(dict(HIT, dataset_id=DATASET_B)))
        self.read_recall(recall(HIT, dict(HIT, text='x', dataset_id=None)))
        assert {c.dataset_status for c in self.connector.claims} == {'entry_matches_declaration', 'caller_declared'}
        assert (self.connector.summary['cross_source_joins'], 'result_dataset_unverified' in self.connector.summary['warnings']) == ('disabled', True)
        self.read_recall(recall(HIT))
        assert self.connector.summary['cross_source_joins'] == 'dataset_id_bound'
        self.read_recall(recall(QA, dict(QA, qa_id='qa-2', used_graph_element_ids=None, time='bad')), session_id=None)
        assert {c.session_status for c in self.connector.claims} == {'unknown'}
        assert {'session_id_missing', 'session_entry_without_graph_refs', 'invalid_timestamp'} <= set(self.connector.summary['warnings'])
        assert self.connector.summary['session_lineage'] == 'unknown'
        # The same result under another dataset ID is a different claim.
        a = {c.native_id_sha256 for c in self.read_recall(recall(HIT)).records if c.record_type == 'cognee_recall_graph'}
        b = {c.native_id_sha256 for c in self.read_recall(recall(dict(HIT, dataset_id=DATASET_B)), dataset_id=DATASET_B).records
             if c.record_type == 'cognee_recall_graph'}
        assert a and not a & b

    def test_retrieved_chunks_and_code_facts_link_to_graph_nodes_without_keeping_names(self):
        self.read(snapshot(nodes=NODES+[node(SYMBOL, 'CodeSymbol', name='invented_fn')]))
        graph = {c.native_id_sha256: c for c in self.connector.claims}
        by_detail = {c.detail: c.native_id_sha256 for c in graph.values()}
        self.read_recall(recall(CITED, CODE))
        cited, code = self.connector.claims
        # Data and chunk IDs hash to the document and chunk claims of a graph export of the same dataset.
        assert set(cited.graph_node_refs) == {by_detail['TextDocument'], by_detail['DocumentChunk']} and cited.code_node_type is None
        assert (code.graph_node_refs, code.code_node_type, code.derivation) == ((graph_hash(graph, 'other'),), 'CodeSymbol', 'retrieved_item')
        assert {c.graph_refs_status for c in self.connector.claims} == {'named'}
        assert 'result_without_provenance_refs' not in self.connector.summary['warnings']
        # Names, paths and lines stay inside the body fingerprint; no claim field holds them.
        assert SECRET not in json.dumps([c.to_dict() for c in self.connector.claims]) and 'invented_fn' not in repr(self.connector.claims)
        # The same IDs under another dataset ID are other nodes.
        self.read_recall(recall(dict(CITED, dataset_id=DATASET_B), dict(CODE, dataset_id=DATASET_B)), dataset_id=DATASET_B)
        assert not set(cited.graph_node_refs + code.graph_node_refs) & {r for c in self.connector.claims for r in c.graph_node_refs}

    def test_results_without_typed_provenance_do_not_guess_a_link(self):
        self.read_recall(recall(HIT, dict(ANSWER, metadata=dict(chunk_id=CHUNK))))
        assert [(c.graph_refs_status, c.derivation) for c in self.connector.claims] == [('none', 'retrieved_item'), ('named', 'generated_answer')]
        assert [w for w in self.connector.summary['warnings'] if w.startswith('result_without')] == ['result_without_provenance_refs']
        # A code fact whose type is not a pinned code-graph type contributes no reference and no type.
        self.read_recall(recall(dict(CODE, raw=dict(CODE['raw'], type='Mystery'))))
        claim, = self.connector.claims
        assert (claim.graph_node_refs, claim.code_node_type, claim.graph_refs_status) == ((), None, 'none')
        assert 'code_fact_type_unrecognized' in self.connector.summary['warnings']
        for bad in (dict(HIT, metadata=dict(chunk_id=1)), dict(HIT, metadata=dict(data_id='')), dict(HIT, metadata=['x']),
                    dict(CODE, raw=dict(CODE['raw'], id=7)), dict(HIT, raw='x')):
            with pytest.raises(ValueError): self.read_recall(recall(bad))
        cognee.RecallClaim(**dict(claim.to_dict(), code_node_type='CodeSymbol'))
        for change in (dict(code_node_type='Mystery'), dict(claim_type='recall_graph', code_node_type='CodeSymbol')):
            with pytest.raises(ValueError): cognee.RecallClaim(**dict(claim.to_dict(), **change))
        with pytest.raises(ValueError): cognee.RecallClaim(**dict(claim.to_dict(), graph_refs_status='named'))

    def test_recall_results_fail_closed_and_stay_bounded(self):
        for bad in ({}, ['text'], recall(dict(QA, used_graph_element_ids=dict(node_ids=[1])))):
            with pytest.raises(ValueError): self.read_recall(bad)
        self.read_recall(recall(dict(source='mystery'), HIT))
        assert self.connector.summary['rows_rejected'] == dict(results=1) and len(self.connector.claims) == 1
        self.read_recall(recall(HIT, HIT))
        assert [c.occurrence for c in self.connector.claims] == [0, 1] and 'duplicate_native_id' in self.connector.summary['warnings']
        with pytest.raises(HostileReport, match='row bound'): self.read_recall(recall(HIT, HIT, HIT), limits=dict(max_rows=3))
        with pytest.raises(ValueError): self.read(recall(HIT), session_id=SESSION)  # session_id belongs to recall_results only
        with pytest.raises(ValueError): self.read_recall(recall(HIT), session_id='bad id')

    def write_archive(self, files=None, **selection):
        """Write a COGX archive directory (JSONL per record kind) and declare it as the selected export."""
        archive = self.root/ARCHIVE_NAME
        shutil.rmtree(archive, ignore_errors=True)
        archive.mkdir(parents=True)
        for name, rows in (cogx() if files is None else files).items():
            lines = [rows] if name == 'manifest.json' else rows
            (archive/name).write_text('\n'.join(json.dumps(row) for row in lines) + '\n')
        return self.read_declared(format='cogx_archive', export=ARCHIVE_NAME, **selection)

    def read_declared(self, **selection):
        self.write(snapshot(), **selection)
        return self.connector.read(self.source)

    def test_cogx_archive_maps_chunks_entities_and_facts_to_the_claims_a_graph_export_gives(self):
        graph = self.read(plain(), format='nodes_edges')
        expected = {r.native_id_sha256: r.record_type for r in graph.records}
        origins = {c.native_id_sha256: (c.claim_type, c.detail, c.origin_status) for c in self.connector.claims}
        result = self.write_archive()
        summary = self.connector.summary
        # The chunk is in documents.jsonl and nodes.jsonl but is one claim; the entity types and documents come from nodes.jsonl.
        assert summary['claims'] == dict(document=1, chunk=1, entity=3, graph_node=0, relation=5)
        assert {r.native_id_sha256: r.record_type for r in result.records if r.record_type != 'cognee_export'} == {
            k: v for k, v in expected.items() if v != 'cognee_export'}
        assert {c.native_id_sha256: (c.claim_type, c.detail, c.origin_status) for c in self.connector.claims} == origins
        assert (summary['export_format'], summary['compatibility'], summary['dataset_lineage']) == ('cogx_archive', 'unverified', 'caller_declared')
        assert summary['archive'] == dict(cogx_version='0.1', records=dict(document=1, entity=2, fact=5, raw_node=3),
                                          entries_not_read=0, migration_revision_sha256=None)
        assert not {'manifest_counts_mismatch', 'archive_entries_not_read', 'duplicate_native_id'} & set(summary['warnings'])

    @pytest.mark.parametrize('dataset_id', [pytest.param(DATASET_A, id='declared_id'), pytest.param(None, id='no_declared_id')])
    def test_cogx_archive_carries_no_dataset_id_so_joins_stay_disabled(self, dataset_id):
        self.write_archive(dataset_id=dataset_id)
        summary = self.connector.summary
        assert (summary['dataset_lineage'], summary['cross_source_joins']) == ('caller_declared' if dataset_id else 'unknown', 'disabled')
        assert 'dataset_lineage_unverified' in summary['warnings']

    def test_cogx_archive_claims_of_dataset_a_and_b_never_share_an_id(self):
        a = {r.native_id_sha256 for r in self.write_archive().records}
        b = {r.native_id_sha256 for r in self.write_archive(dataset='dataset-b', dataset_id=DATASET_B).records}
        assert not a & b

    def test_cogx_archive_entity_timestamps_are_native_and_chunk_copies_keep_their_origin(self):
        self.write_archive()
        entities = [c for c in self.claims('entity') if c.detail == 'Entity']
        assert {(c.timestamp_status, c.original_at) for c in entities} == {('native', CREATED_ISO)}
        chunk, = self.claims('chunk')
        assert chunk.origin_status == 'traced'

    @pytest.mark.parametrize('name', [pytest.param('permissions.json', id='lower_case'), pytest.param('Permissions.JSON', id='mixed_case')])
    @pytest.mark.parametrize('kind', ['regular', 'symlink'])
    def test_cogx_archive_with_a_permissions_file_is_refused_without_opening_it(self, name, kind):
        files = cogx()
        self.write_archive(files)
        target = self.root/ARCHIVE_NAME/name
        if kind == 'regular': target.write_text(json.dumps(dict(owner=dict(email='invented@example.invalid', hashed_password=SECRET))))
        else: target.symlink_to(self.root/'absent-permissions-target')
        opened = []
        real = cognee.read_regular
        def spy(path, limit): opened.append(path.name); return real(path, limit)
        with patch.object(cognee, 'read_regular', spy), pytest.raises(ValueError, match='permissions') as error:
            self.connector.read(self.source)
        assert opened == [] and SECRET not in str(error.value)

    @pytest.mark.parametrize('change', [
        pytest.param(dict(source_system='mem0'), id='another_producer'), pytest.param(dict(source_system=None), id='no_producer'),
        pytest.param(dict(cogx_version='0.2'), id='newer_minor'), pytest.param(dict(cogx_version='1.0'), id='newer_major'),
        pytest.param(dict(cogx_version=None), id='no_version'), pytest.param(dict(counts=[]), id='counts_list'),
        pytest.param(dict(counts=dict(fact=-1)), id='negative_count'), pytest.param(dict(migration_revision=7), id='revision_integer')])
    def test_cogx_archive_manifest_changes_that_the_pin_does_not_describe_are_refused(self, change):
        files = cogx()
        files['manifest.json'] = {k: v for k, v in dict(files['manifest.json'], **change).items() if v is not None}
        with pytest.raises(ValueError): self.write_archive(files)

    @pytest.mark.parametrize('damage', [
        pytest.param(lambda f: f.pop('manifest.json'), id='no_manifest'),
        pytest.param(lambda f: f['facts.jsonl'][0].update(external_system='mem0'), id='fact_from_another_system'),
        pytest.param(lambda f: f['entities.jsonl'][0].update(kind='fact'), id='kind_in_the_wrong_file'),
        pytest.param(lambda f: f['nodes.jsonl'].append('x'), id='raw_node_string')])
    def test_cogx_archive_records_that_do_not_match_the_pinned_writer_are_refused(self, damage):
        files = cogx()
        damage(files)
        with pytest.raises(ValueError): self.write_archive(files)

    @pytest.mark.parametrize('line', [pytest.param('{"id": "a", "id": "b"}', id='duplicate_key'), pytest.param('{"id": NaN}', id='nan'),
                                      pytest.param('{"id": ', id='truncated'), pytest.param('[1]', id='array_row')])
    def test_cogx_archive_malformed_jsonl_lines_reject_the_archive(self, line):
        files = cogx()
        self.write_archive(files)
        with (self.root/ARCHIVE_NAME/'nodes.jsonl').open('a') as handle: handle.write(line+'\n')
        with pytest.raises(ValueError): self.connector.read(self.source)

    def test_cogx_archive_selection_must_name_a_plain_directory(self):
        self.write_archive()
        (self.root/'exports/packed.cogx.tar.gz').write_bytes(b'\x1f\x8b invented')
        with pytest.raises(ValueError, match='directory'): self.read_declared(format='cogx_archive', export='exports/packed.cogx.tar.gz')
        (self.root/'exports/linked').symlink_to(self.root/ARCHIVE_NAME)
        with pytest.raises(ValueError, match='symlink'): self.read_declared(format='cogx_archive', export='exports/linked')
        with pytest.raises(ValueError, match='directory'): self.read_declared(format='cogx_archive', export='exports/cognee.json')

    def test_cogx_archive_files_the_writer_does_not_emit_are_never_read_and_are_counted(self):
        files = cogx()
        self.write_archive(files)
        for name in ('memories.jsonl', 'notes.txt'): (self.root/ARCHIVE_NAME/name).write_text(SECRET)
        (self.root/ARCHIVE_NAME/'sub').mkdir()
        opened = []
        real = cognee.read_regular
        def spy(path, limit): opened.append(path.name); return real(path, limit)
        with patch.object(cognee, 'read_regular', spy): self.connector.read(self.source)
        assert sorted(opened) == ['documents.jsonl', 'entities.jsonl', 'facts.jsonl', 'manifest.json', 'nodes.jsonl']
        assert self.connector.summary['archive']['entries_not_read'] == 3
        assert 'archive_entries_not_read' in self.connector.summary['warnings']

    def test_cogx_archive_manifest_counts_that_disagree_with_the_records_are_explicit(self):
        files = cogx()
        files['facts.jsonl'].pop()
        self.write_archive(files)
        assert 'manifest_counts_mismatch' in self.connector.summary['warnings']
        assert self.connector.summary['claims']['relation'] == 4
        files = cogx()
        files['manifest.json']['counts'] = dict(files['manifest.json']['counts'], episode=2)
        self.write_archive(files)
        assert 'manifest_counts_mismatch' in self.connector.summary['warnings']

    def test_cogx_archive_chunk_copy_without_a_raw_node_has_no_traced_origin(self):
        files = cogx()
        files['nodes.jsonl'] = [row for row in files['nodes.jsonl'] if row['id'] != CHUNK]
        # Neither a raw node's document_id nor an is_part_of fact names its document: the copy's text alone traces nothing.
        files['facts.jsonl'] = [row for row in files['facts.jsonl'] if row['predicate'] != 'is_part_of']
        self.write_archive(files)
        chunk, = self.claims('chunk')
        assert (chunk.origin_status, chunk.origin_document_sha256) == ('missing', None)
        assert 'chunk_origin_missing' in self.connector.summary['warnings']

    def test_cogx_archive_chunk_copy_traces_through_its_is_part_of_fact(self):
        files = cogx()
        files['nodes.jsonl'] = [row for row in files['nodes.jsonl'] if row['id'] != CHUNK]
        self.write_archive(files)
        chunk, = self.claims('chunk')
        document, = self.claims('document')
        assert (chunk.origin_status, chunk.origin_document_sha256) == ('traced', document.native_id_sha256)

    def test_cogx_archive_repeated_records_are_further_occurrences(self):
        files = cogx()
        files['facts.jsonl'].append(dict(files['facts.jsonl'][0]))
        files['entities.jsonl'].append(dict(files['entities.jsonl'][0]))
        self.write_archive(files)
        assert sorted(c.occurrence for c in self.claims('relation')) == [0, 0, 0, 0, 0, 1]
        assert 'duplicate_native_id' in self.connector.summary['warnings']

    def test_cogx_archive_records_without_identity_or_endpoints_are_rejected_and_counted(self):
        files = cogx()
        del files['entities.jsonl'][0]['external_id']
        del files['facts.jsonl'][0]['subject_ref']
        self.write_archive(files)
        assert self.connector.summary['rows_rejected'] == dict(nodes=1, edges=1)
        assert {'node_without_native_identity', 'edge_without_endpoints'} <= set(self.connector.summary['warnings'])

    def test_cogx_archive_manifest_revision_is_kept_as_a_hash_only(self):
        files = cogx(migration_revision='invented-revision-7')
        self.write_archive(files)
        recorded = self.connector.summary['archive']['migration_revision_sha256']
        assert recorded == digest(['cognee_migration_revision', 'invented-revision-7'])
        assert 'invented-revision-7' not in json.dumps(self.connector.summary)

    @pytest.mark.parametrize(('bound', 'error'), [
        pytest.param(dict(max_rows=11), 'row bound', id='rows'), pytest.param(dict(max_fields=3), 'field bound', id='fields'),
        pytest.param(dict(max_json_containers=5), 'container', id='containers')])
    def test_cogx_archive_bounds_abort_before_mapping(self, bound, error):
        with patch.object(cognee, 'parse', side_effect=AssertionError('mapped')), pytest.raises(HostileReport, match=error):
            self.write_archive(limits=bound)

    def test_cogx_archive_row_bound_admits_exactly_the_records_it_names(self):
        assert len(self.write_archive(limits=dict(max_rows=12)).records) == 11

    def test_cogx_archive_bytes_are_bounded_across_all_files_not_only_per_file(self):
        self.write_archive()
        largest = max(path.stat().st_size for path in (self.root/ARCHIVE_NAME).iterdir())
        with patch.object(cognee, 'parse', side_effect=AssertionError('mapped')), pytest.raises(HostileReport, match='byte bound'):
            self.read_declared(format='cogx_archive', export=ARCHIVE_NAME, limits=dict(max_export_bytes=largest))

    def test_cogx_archive_entry_count_is_bounded(self):
        self.write_archive()
        for index in range(cognee.MAX_ARCHIVE_ENTRIES+1): (self.root/ARCHIVE_NAME/f'extra-{index}.txt').write_text('x')
        with pytest.raises(HostileReport, match='entry bound'): self.connector.read(self.source)

    def test_cogx_archive_change_during_capture_is_refused(self):
        self.write_archive()
        with patch.object(cognee, 'regular_sha256', lambda path, limit: '0'*64):
            with pytest.raises(ValueError, match='changed during capture'): self.connector.read(self.source)

    def test_cli_imports_a_cogx_archive_as_hashes_only_and_refuses_one_with_credentials(self):
        self.write_archive()
        code, preview = self.invoke('preview')
        assert (code, json.loads(preview)['cognee_summary']['export_format']) == (0, 'cogx_archive')
        code, receipt = self.invoke('import')
        assert (code, json.loads(receipt)['imported']) == (0, 11)
        raw = (self.root/'private/cache.json').read_text()
        for text in (SECRET, 'Invented', 'node-', DATASET_A, 'works_at', ARCHIVE_NAME, 'invented-dataset'):
            assert text not in raw
        (self.root/ARCHIVE_NAME/'permissions.json').write_text(SECRET)
        before = (self.root/'private/cache.json').read_bytes()
        assert self.invoke('import')[0] == 2
        assert (self.root/'private/cache.json').read_bytes() == before

    def test_capture_change_is_refused(self):
        with patch.object(cognee, 'regular_sha256', lambda path, limit: '0'*64):
            with pytest.raises(ValueError, match='changed during capture'): self.connector.read(self.source)

    def test_cli_import_holds_hashes_only_and_never_loads_cognee(self):
        self.invoke('status')
        # Cognee is made unimportable; the CLI's own lazy imports are loaded above so the patch does not unload them.
        with patch.dict(sys.modules, {'cognee': None}):
            code, preview = self.invoke('preview')
            assert (code, json.loads(preview)['cognee_summary']['profile']) == (0, 'cognee-export-v1')
            code, receipt = self.invoke('import')
        assert (code, json.loads(receipt)['imported']) == (0, 11)
        raw = (self.root/'private/cache.json').read_text()
        for text in (SECRET, 'Invented', 'node-', DATASET_A, 'works_at', 'exports/cognee.json'):
            assert text not in raw
        assert json.loads(self.invoke('import')[1])['skipped'] == 11
        before = (self.root/'private/cache.json').read_bytes()
        (self.root/'exports/cognee.json').write_text(json.dumps(snapshot(DATASET_B)))
        assert self.invoke('import')[0] == 2
        assert (self.root/'private/cache.json').read_bytes() == before
