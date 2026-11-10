"""Synthetic relationship receipts; no private histories or asserted task grades."""
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import FrozenInstanceError, replace
import io
import json
from unittest.mock import Mock, patch

import pytest

from tests.support.github_case import GitHubPytestCase
from tests.unit.sources.test_source_execution import metadata_for

from agent_harness_lab.cli import main
from agent_harness_lab.sources.execution import RecordRef, capture_snapshot
from agent_harness_lab.sources.graph import EvidenceGraph, JoinReceipt
from agent_harness_lab.sources.github import GitHubAPI, GitHubSelection
from agent_harness_lab.sources.registry import Capability, Registry
from agent_harness_lab.sources.schema import EvidenceRecord, SourceSpec, digest
from agent_harness_lab.sources.snapshot import LocalExport, Snapshot
from agent_harness_lab.sources.store import CommitUncertain, SourceStore


def graph_for(source, records):
    refs = tuple(RecordRef(r.source_sha256, r.native_id_sha256, r.revision_sha256) for r in records)
    join = JoinReceipt(refs[0], refs[1], 'derived_from', 'synthetic.claim', 'synthetic-v1', refs)
    return EvidenceGraph(source.source_key, source.project_sha256, source.scope_sha256, source.version, refs, (join,))


class GraphFixture:
    @pytest.fixture(autouse=True)
    def _graph(self):
        self.source = SourceSpec('local_file', 'ahl-metadata', 'v3', digest('root'), digest('project'), digest('scope'),
            identity_provenance='source_declared_id', enabled=True)
        self.records = tuple(EvidenceRecord(self.source.source_key, digest(n), digest(('revision', n)), digest('SYNTHETIC_SECRET'),
            'contextual_assertion', 'synthetic_claim', '2026-01-01T00:00:00Z') for n in ('original', 'copy'))
        self.graph = graph_for(self.source, self.records)
        self.join = self.graph.joins[0]


class TestGraphContract(GraphFixture):
    def test_frozen_canonical_round_trip_and_unknown_independence(self):
        restored = EvidenceGraph.parse(json.loads(json.dumps(self.graph.to_dict())))
        assert restored == self.graph
        assert restored.sha256 == replace(self.graph, nodes=tuple(reversed(self.graph.nodes))).sha256
        restored.validate(self.source, self.records)
        assert restored.summary()['independent_roots'] is None
        assert restored.summary()['statuses']['candidate'] == 1
        assert not restored.summary()['verification_authorized']
        with pytest.raises(FrozenInstanceError): restored.nodes = ()
        assert 'SYNTHETIC_SECRET' not in json.dumps(restored.to_dict())

    @pytest.mark.parametrize('status,basis,reason', [pytest.param('verified', 'native_fields', 'native_proof', id='verified'),
                                                     pytest.param('reviewed', 'reviewed_annotation', 'reviewed_proof', id='reviewed')])
    def test_claimed_native_or_reviewed_proofs_do_not_self_certify(self, status, basis, reason):
        claimed = replace(self.join, status=status, basis=basis, reason=reason)
        graph = replace(self.graph, joins=(claimed,))
        with pytest.raises(ValueError): graph.validate(self.source, self.records)

    @pytest.mark.parametrize('change', [
        pytest.param(dict(source_sha256=digest('foreign')), id='foreign-source'),
        pytest.param(dict(project_sha256=digest('foreign')), id='foreign-project'),
        pytest.param(dict(scope_sha256=digest('foreign')), id='foreign-scope'), pytest.param(dict(adapter_version='other'), id='other-adapter')])
    def test_foreign_graph_identity_is_rejected(self, change):
        with pytest.raises(ValueError): replace(self.graph, **change).validate(self.source, self.records)

    @pytest.mark.parametrize('change', [pytest.param(dict(source_sha256=digest('foreign')), id='foreign-source'),
                                        pytest.param(dict(revision_sha256=digest('missing')), id='missing-revision')])
    def test_foreign_source_and_missing_native_revisions_are_rejected(self, change):
        ref = replace(self.join.left, **change)
        graph = replace(self.graph, nodes=(ref, self.join.right), joins=(replace(self.join, left=ref),))
        with pytest.raises(ValueError): graph.validate(self.source, self.records)

    def test_dangling_join_endpoints_and_evidence_must_be_members(self):
        missing = replace(self.join.left, native_id_sha256=digest('missing'))
        for join in (replace(self.join, right=missing), replace(self.join, evidence=(missing,))):
            with pytest.raises(ValueError): replace(self.graph, joins=(join,)).validate(self.source, self.records)

    def test_missing_and_conflicting_links_remain_explicit_without_fabricated_ids(self):
        missing = replace(self.join, right=None, status='unlinked', reason='missing_identity', evidence=())
        conflict = replace(self.join, status='conflicted', reason='conflicting_evidence')
        graph = replace(self.graph, joins=(missing, conflict))
        graph.validate(self.source, self.records)
        assert graph.summary()['statuses']['unlinked'] == 1
        assert graph.summary()['statuses']['conflicted'] == 1
        assert missing.right is None
        assert graph.summary()['independent_roots'] is None

    @pytest.mark.parametrize('change', [
        pytest.param(lambda join: dict(namespace='/private/path'), id='private-path-namespace'),
        pytest.param(lambda join: dict(relation='SYNTHETIC_SECRET'), id='unknown-relation'),
        pytest.param(lambda join: dict(evidence=[join.left]), id='list-evidence'),
        pytest.param(lambda join: dict(status='verified'), id='verified-status'),
        pytest.param(lambda join: dict(reason='arbitrary'), id='arbitrary-reason'),
        pytest.param(lambda join: dict(effective_from='2026-02-01T00:00:00Z', effective_until='2026-01-01T00:00:00Z'), id='reversed-interval')])
    def test_strict_join_enums_and_validity_intervals(self, change):
        with pytest.raises(ValueError): replace(self.join, **change(self.join))

    @pytest.mark.parametrize('change', [
        pytest.param(lambda graph: dict(nodes=graph.nodes*2), id='nodes'),
        pytest.param(lambda graph: dict(joins=(graph.joins[0], graph.joins[0])), id='joins'),
        pytest.param(lambda graph: dict(nodes=list(graph.nodes)), id='list-nodes'),
        pytest.param(lambda graph: dict(joins=(graph.joins[0],)*20001), id='too-many-joins'),
        pytest.param(lambda graph: dict(schema_version=2), id='schema_version')])
    def test_strict_graph_bounds_and_duplicates(self, change):
        with pytest.raises(ValueError): replace(self.graph, **change(self.graph))

    def test_strict_enums_bounds_duplicates_and_validity_intervals(self):
        with pytest.raises(ValueError): EvidenceGraph.parse({**self.graph.to_dict(), 'text': 'SYNTHETIC_SECRET'})


class TestGraphIntegration(GraphFixture):
    @pytest.fixture(autouse=True)
    def _integration(self, _graph, tmp_path):
        self.root = tmp_path
        self.source = replace(self.source, root_sha256=digest(str(self.root.resolve())))
        self.records = tuple(replace(r, source_sha256=self.source.source_key) for r in self.records)
        self.graph = graph_for(self.source, self.records); self.join = self.graph.joins[0]
        self.scope = dict(project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256)
        self.store = SourceStore(self.root/'journal.json', **self.scope)
        self.manifest, self.data = self.root/'source.json', self.root/'records.json'
        self.write_export()

    def write_export(self, graph=None, metadata=None):
        self.manifest.write_text(json.dumps(dict(schema_version=1, source=self.source.to_dict(), data='records.json')))
        self.data.write_text(json.dumps(dict(schema_version=3, records=[r.to_dict() for r in self.records],
            execution_metadata=metadata.to_dict() if metadata is not None else None,
            evidence_graph=(graph or self.graph).to_dict())))

    def invoke(self, operation):
        output, error = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            code = main(['sources', operation, '--manifest', str(self.manifest), '--cache', str(self.store.path),
                '--project-sha256', self.source.project_sha256, '--scope-sha256', self.source.scope_sha256])
        for secret in ('SYNTHETIC_SECRET', str(self.root)): assert secret not in output.getvalue()+error.getvalue()
        return code, json.loads(output.getvalue()) if output.getvalue() else None

    def ingest(self, *, graph=None, before=None, records=None, metadata=None):
        return self.store.ingest(self.source, self.records if records is None else records, cursor_before=before,
            snapshot_sha256=digest('raw-snapshot'), graph=graph or self.graph, metadata=metadata)

    def test_v3_preview_import_status_and_private_historical_graph(self):
        code, preview = self.invoke('preview'); assert code == 0
        assert preview['evidence_graph']['statuses']['candidate'] == 1
        code, receipt = self.invoke('import'); assert code == 0
        assert self.invoke('import')[1]['imported'] == 0
        code, status = self.invoke('status'); assert code == 0
        assert status['snapshot_sha256'] == preview['checkpoint_snapshot_sha256']
        assert status['evidence_graph'] == self.graph.summary()
        assert self.store.graph(self.source, receipt['cursor_after_sha256']) == self.graph
        assert self.store.capture(self.source) is None
        assert self.store.path.stat().st_mode & 0o777 == 0o600
        for secret in ('SYNTHETIC_SECRET', str(self.root)): assert secret not in self.store.path.read_text()

    def test_graph_only_changes_recurrence_and_order_do_not_duplicate_records(self):
        first = self.ingest()
        reordered = replace(self.graph, nodes=tuple(reversed(self.graph.nodes)))
        replay = self.ingest(graph=reordered, before=first.cursor_after_sha256)
        assert replay.cursor_after_sha256 == first.cursor_after_sha256
        newer = replace(self.graph, joins=(replace(self.join, relation='cites'),))
        second = self.ingest(graph=newer, before=replay.cursor_after_sha256, records=())
        third = self.ingest(before=second.cursor_after_sha256)
        assert second.imported == 0
        assert len({r.cursor_after_sha256 for r in (first, second, third)}) == 3
        assert self.store.graph(self.source, first.cursor_after_sha256) == self.graph
        assert self.store.graph(self.source, second.cursor_after_sha256) == newer
        assert self.store.status(self.source)['revisions'] == 2

    def test_execution_metadata_and_graph_share_one_checkpoint_and_history(self):
        metadata = metadata_for(self.source, self.records[0])
        self.write_export(metadata=metadata)
        assert self.invoke('import')[0] == 0
        assert self.store.capture(self.source) == metadata
        assert self.store.graph(self.source) == self.graph
        assert self.store.status(self.source)['snapshot_sha256'] == capture_snapshot(self.source, LocalExport(self.manifest).read(self.source).sha256, metadata, self.graph)

    def test_verified_labels_are_rejected_by_export_registry_and_direct_store(self):
        for status, basis, reason in (('verified', 'native_fields', 'native_proof'), ('reviewed', 'reviewed_annotation', 'reviewed_proof')):
            claimed = replace(self.join, status=status, basis=basis, reason=reason)
            graph = replace(self.graph, joins=(claimed,)); self.write_export(graph)
            assert self.invoke('import')[0] == 2
            assert not self.store.path.exists()
            registry = Registry(); registry.register(Capability('local_file', 'ahl-metadata', 'v3', ('contextual_assertion',)),
                Mock(read=Mock(return_value=Snapshot(digest('raw'), self.records, graph=graph))))
            with pytest.raises(ValueError): registry.preview(self.source, **self.scope)
            with pytest.raises(ValueError): self.ingest(graph=graph)
            assert not self.store.path.exists()

    def test_rejected_graphs_and_byte_caps_leave_committed_bytes_unchanged(self):
        first = self.ingest(); original = self.store.path.read_bytes()
        for graph in (replace(self.graph, project_sha256=digest('foreign')),
                      replace(self.graph, nodes=(replace(self.join.left, revision_sha256=digest('missing')), self.join.right))):
            with pytest.raises(ValueError): self.ingest(graph=graph, before=first.cursor_after_sha256)
            assert self.store.path.read_bytes() == original
        limited = SourceStore(self.root/'limited.json', **self.scope, max_bytes=1024)
        with pytest.raises(ValueError): limited.ingest(self.source, self.records, snapshot_sha256=digest('raw'), graph=self.graph)
        assert not limited.path.exists()

    def test_v3_journal_migration_preserves_hashes_and_waits_for_success(self):
        metadata = metadata_for(self.source, self.records[0])
        seed = self.store.ingest(self.source, self.records, snapshot_sha256=digest('old'), metadata=metadata)
        state = json.loads(self.store.path.read_text()); state['schema_version'] = 3
        for envelope in state['captures'][self.source.source_key].values(): envelope.pop('graph')
        self.store.path.write_text(json.dumps(state)); original = self.store.path.read_bytes()
        assert self.store.capture(self.source) == metadata
        assert self.store.graph(self.source) is None
        assert self.store.path.read_bytes() == original
        with pytest.raises(ValueError): self.ingest(graph=replace(self.graph, scope_sha256=digest('foreign')), before=seed.cursor_after_sha256)
        assert self.store.path.read_bytes() == original
        replay = self.store.ingest(self.source, self.records, cursor_before=seed.cursor_after_sha256,
            snapshot_sha256=digest('old'), metadata=metadata)
        assert replay.cursor_after_sha256 == seed.cursor_after_sha256
        assert self.store.status(self.source)['checkpoints'] == 1
        current = self.ingest(before=seed.cursor_after_sha256)
        migrated = json.loads(self.store.path.read_text())
        assert migrated['schema_version'] == 4
        assert migrated['records'] == state['records']
        assert migrated['cursors'][self.source.source_key][0] == seed.cursor_after_sha256
        assert self.store.capture(self.source, seed.cursor_after_sha256) == metadata
        assert self.store.graph(self.source, seed.cursor_after_sha256) is None
        assert self.store.graph(self.source, current.cursor_after_sha256) == self.graph

    def test_modified_graph_cannot_relabel_a_committed_checkpoint(self):
        first = self.ingest(); state = json.loads(self.store.path.read_text())
        state['captures'][self.source.source_key][first.cursor_after_sha256]['graph']['joins'][0]['relation'] = 'supports'
        self.store.path.write_text(json.dumps(state)); malformed = self.store.path.read_bytes()
        with pytest.raises(ValueError): self.store.status(self.source)
        assert self.store.path.read_bytes() == malformed

    def test_late_revision_does_not_rebind_historical_graph_endpoints(self):
        first = self.ingest()
        revised = (self.records[0], replace(self.records[1], revision_sha256=digest('later')))
        newer = graph_for(self.source, revised)
        self.ingest(graph=newer, records=revised, before=first.cursor_after_sha256)
        assert self.store.graph(self.source, first.cursor_after_sha256) == self.graph
        assert self.store.graph(self.source) == newer
        assert self.graph.joins[0].right.revision_sha256 != newer.joins[0].right.revision_sha256

    def test_ambiguous_write_keeps_graph_and_records_in_the_same_commit(self):
        from agent_harness_lab.sources import store as module
        commit = module.atomic_json
        def uncertain(path, value):
            commit(path, value)
            raise OSError('synthetic directory sync failure')
        with patch.object(module, 'atomic_json', side_effect=uncertain):
            with pytest.raises(CommitUncertain): self.ingest()
        assert self.store.status(self.source)['revisions'] == 2
        assert self.store.graph(self.source) == self.graph


class TestGitHubGraph(GitHubPytestCase):
    @pytest.fixture(autouse=True)
    def _graph(self, _github):
        self.selection = replace(self.selection, schema_version=5, details=('graph',))
        self.source = replace(self.source, version=self.selection.source_version, scope_sha256=self.selection.sha256)

    def test_v5_maps_all_relationships_to_exact_record_revisions_without_extra_requests(self):
        client, snapshot = self.read()
        assert len(snapshot.graph.nodes) == len(snapshot.records)
        assert len(snapshot.graph.joins) == len(client.summary['relations'])
        assert all(j.status == 'candidate' and j.basis == 'native_fields' for j in snapshot.graph.joins)
        assert snapshot.graph.summary()['independent_roots'] is None
        assert len(self.targets) == 14
        revisions = {(r.source_sha256, r.native_id_sha256, r.revision_sha256) for r in snapshot.records}
        assert all(j.left.key in revisions and j.right.key in revisions for j in snapshot.graph.joins)

    def test_github_import_retains_prior_graph_after_review_revision_changes(self, tmp_path):
        scope = dict(project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256)
        registry = Registry(); registry.register(Capability('api', 'github', self.source.version,
            ('contextual_assertion', 'development_artifact')), GitHubAPI(self.base, self.selection))
        store = SourceStore(tmp_path/'cache.json', **scope)
        first = registry.ingest(self.source, store, **scope); before = store.graph(self.source)
        self.review['body'] = 'SYNTHETIC_EDIT'
        second = registry.ingest(self.source, store, **scope)
        assert first.cursor_after_sha256 != second.cursor_after_sha256
        assert store.graph(self.source, first.cursor_after_sha256) == before
        assert store.graph(self.source) != before
        assert store.status(self.source)['evidence_graph']['statuses']['verified'] == 0
        assert 'SYNTHETIC_' not in store.path.read_text()

    def test_older_versions_cannot_enable_graphs_and_remain_serializable(self):
        for version in (1, 2, 3, 4):
            with pytest.raises(ValueError): replace(self.selection, schema_version=version)
            selection = replace(self.selection, schema_version=version, details=())
            assert GitHubSelection.parse(selection.to_dict()) == selection
            source = replace(self.source, version=selection.source_version, scope_sha256=selection.sha256)
            assert GitHubAPI(self.base, selection).read(source).graph is None


class TestUnlimitedGraph(GraphFixture):
    def test_complete_large_graph_retains_nodes_joins_and_evidence(self):
        records = tuple(replace(self.records[0], native_id_sha256=digest(i)) for i in range(10001))
        refs = tuple(RecordRef(r.source_sha256, r.native_id_sha256, r.revision_sha256) for r in records)
        joins = tuple(replace(self.join, left=refs[0], right=refs[1], evidence=refs[:9],
            resolver_version='synthetic.'+str(i)) for i in range(20001))
        graph = EvidenceGraph(self.source.source_key, self.source.project_sha256, self.source.scope_sha256,
            self.source.version, refs, joins)
        restored = EvidenceGraph.parse(graph.to_dict())
        restored.validate(self.source, records)
        assert restored == graph
        assert (len(restored.nodes), len(restored.joins)) == (10001, 20001)
        with pytest.raises(ValueError): replace(graph, nodes=refs+(refs[0],))
