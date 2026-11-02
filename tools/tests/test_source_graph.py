"""Synthetic relationship receipts; no private histories or asserted task grades."""
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import FrozenInstanceError, replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

if __package__:
    from .test_source_execution import metadata_for
    from .test_source_github import GitHubFixture
else:
    from test_source_execution import metadata_for
    from test_source_github import GitHubFixture

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
    def setUp(self):
        self.source = SourceSpec('local_file', 'ahl-metadata', 'v3', digest('root'), digest('project'), digest('scope'),
            identity_provenance='source_declared_id', enabled=True)
        self.records = tuple(EvidenceRecord(self.source.source_key, digest(n), digest(('revision', n)), digest('SYNTHETIC_SECRET'),
            'contextual_assertion', 'synthetic_claim', '2026-01-01T00:00:00Z') for n in ('original', 'copy'))
        self.graph = graph_for(self.source, self.records)
        self.join = self.graph.joins[0]


class GraphContractTests(GraphFixture, unittest.TestCase):
    def test_frozen_canonical_round_trip_and_unknown_independence(self):
        restored = EvidenceGraph.parse(json.loads(json.dumps(self.graph.to_dict())))
        self.assertEqual(restored, self.graph)
        self.assertEqual(restored.sha256, replace(self.graph, nodes=tuple(reversed(self.graph.nodes))).sha256)
        restored.validate(self.source, self.records)
        self.assertIsNone(restored.summary()['independent_roots'])
        self.assertEqual(restored.summary()['statuses']['candidate'], 1)
        self.assertFalse(restored.summary()['verification_authorized'])
        with self.assertRaises(FrozenInstanceError): restored.nodes = ()
        self.assertNotIn('SYNTHETIC_SECRET', json.dumps(restored.to_dict()))

    def test_claimed_native_or_reviewed_proofs_do_not_self_certify(self):
        for status, basis, reason in (('verified', 'native_fields', 'native_proof'),
                                     ('reviewed', 'reviewed_annotation', 'reviewed_proof')):
            claimed = replace(self.join, status=status, basis=basis, reason=reason)
            graph = replace(self.graph, joins=(claimed,))
            with self.subTest(status=status), self.assertRaises(ValueError): graph.validate(self.source, self.records)

    def test_foreign_scope_source_and_missing_native_revisions_are_rejected(self):
        for change in (dict(source_sha256=digest('foreign')), dict(project_sha256=digest('foreign')),
                       dict(scope_sha256=digest('foreign')), dict(adapter_version='other')):
            with self.subTest(change=change), self.assertRaises(ValueError): replace(self.graph, **change).validate(self.source, self.records)
        for ref in (replace(self.join.left, source_sha256=digest('foreign')), replace(self.join.left, revision_sha256=digest('missing'))):
            graph = replace(self.graph, nodes=(ref, self.join.right), joins=(replace(self.join, left=ref),))
            with self.assertRaises(ValueError): graph.validate(self.source, self.records)

    def test_dangling_join_endpoints_and_evidence_must_be_members(self):
        missing = replace(self.join.left, native_id_sha256=digest('missing'))
        for join in (replace(self.join, right=missing), replace(self.join, evidence=(missing,))):
            with self.assertRaises(ValueError): replace(self.graph, joins=(join,)).validate(self.source, self.records)

    def test_missing_and_conflicting_links_remain_explicit_without_fabricated_ids(self):
        missing = replace(self.join, right=None, status='unlinked', reason='missing_identity', evidence=())
        conflict = replace(self.join, status='conflicted', reason='conflicting_evidence')
        graph = replace(self.graph, joins=(missing, conflict))
        graph.validate(self.source, self.records)
        self.assertEqual(graph.summary()['statuses']['unlinked'], 1)
        self.assertEqual(graph.summary()['statuses']['conflicted'], 1)
        self.assertIsNone(missing.right)
        self.assertIsNone(graph.summary()['independent_roots'])

    def test_strict_enums_bounds_duplicates_and_validity_intervals(self):
        for change in (dict(namespace='/private/path'), dict(relation='SYNTHETIC_SECRET'), dict(evidence=[self.join.left]),
                       dict(status='verified'), dict(reason='arbitrary'),
                       dict(effective_from='2026-02-01T00:00:00Z', effective_until='2026-01-01T00:00:00Z')):
            with self.subTest(change=change), self.assertRaises(ValueError): replace(self.join, **change)
        for change in (dict(nodes=self.graph.nodes*2), dict(joins=(self.join, self.join)),
                       dict(nodes=list(self.graph.nodes)), dict(joins=(self.join,)*20001), dict(schema_version=2)):
            with self.subTest(change=next(iter(change))), self.assertRaises(ValueError): replace(self.graph, **change)
        with self.assertRaises(ValueError): EvidenceGraph.parse({**self.graph.to_dict(), 'text': 'SYNTHETIC_SECRET'})


class GraphIntegrationTests(GraphFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
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
        for secret in ('SYNTHETIC_SECRET', str(self.root)): self.assertNotIn(secret, output.getvalue()+error.getvalue())
        return code, json.loads(output.getvalue()) if output.getvalue() else None

    def ingest(self, *, graph=None, before=None, records=None, metadata=None):
        return self.store.ingest(self.source, self.records if records is None else records, cursor_before=before,
            snapshot_sha256=digest('raw-snapshot'), graph=graph or self.graph, metadata=metadata)

    def test_v3_preview_import_status_and_private_historical_graph(self):
        code, preview = self.invoke('preview'); self.assertEqual(code, 0)
        self.assertEqual(preview['evidence_graph']['statuses']['candidate'], 1)
        code, receipt = self.invoke('import'); self.assertEqual(code, 0)
        self.assertEqual(self.invoke('import')[1]['imported'], 0)
        code, status = self.invoke('status'); self.assertEqual(code, 0)
        self.assertEqual(status['snapshot_sha256'], preview['checkpoint_snapshot_sha256'])
        self.assertEqual(status['evidence_graph'], self.graph.summary())
        self.assertEqual(self.store.graph(self.source, receipt['cursor_after_sha256']), self.graph)
        self.assertIsNone(self.store.capture(self.source))
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        for secret in ('SYNTHETIC_SECRET', str(self.root)): self.assertNotIn(secret, self.store.path.read_text())

    def test_graph_only_changes_recurrence_and_order_do_not_duplicate_records(self):
        first = self.ingest()
        reordered = replace(self.graph, nodes=tuple(reversed(self.graph.nodes)))
        replay = self.ingest(graph=reordered, before=first.cursor_after_sha256)
        self.assertEqual(replay.cursor_after_sha256, first.cursor_after_sha256)
        newer = replace(self.graph, joins=(replace(self.join, relation='cites'),))
        second = self.ingest(graph=newer, before=replay.cursor_after_sha256, records=())
        third = self.ingest(before=second.cursor_after_sha256)
        self.assertEqual(second.imported, 0)
        self.assertEqual(len({r.cursor_after_sha256 for r in (first, second, third)}), 3)
        self.assertEqual(self.store.graph(self.source, first.cursor_after_sha256), self.graph)
        self.assertEqual(self.store.graph(self.source, second.cursor_after_sha256), newer)
        self.assertEqual(self.store.status(self.source)['revisions'], 2)

    def test_execution_metadata_and_graph_share_one_checkpoint_and_history(self):
        metadata = metadata_for(self.source, self.records[0])
        self.write_export(metadata=metadata)
        self.assertEqual(self.invoke('import')[0], 0)
        self.assertEqual(self.store.capture(self.source), metadata)
        self.assertEqual(self.store.graph(self.source), self.graph)
        self.assertEqual(self.store.status(self.source)['snapshot_sha256'],
            capture_snapshot(self.source, LocalExport(self.manifest).read(self.source).sha256, metadata, self.graph))

    def test_verified_labels_are_rejected_by_export_registry_and_direct_store(self):
        for status, basis, reason in (('verified', 'native_fields', 'native_proof'), ('reviewed', 'reviewed_annotation', 'reviewed_proof')):
            claimed = replace(self.join, status=status, basis=basis, reason=reason)
            graph = replace(self.graph, joins=(claimed,)); self.write_export(graph)
            self.assertEqual(self.invoke('import')[0], 2)
            self.assertFalse(self.store.path.exists())
            registry = Registry(); registry.register(Capability('local_file', 'ahl-metadata', 'v3', ('contextual_assertion',)),
                Mock(read=Mock(return_value=Snapshot(digest('raw'), self.records, graph=graph))))
            with self.assertRaises(ValueError): registry.preview(self.source, **self.scope)
            with self.assertRaises(ValueError): self.ingest(graph=graph)
            self.assertFalse(self.store.path.exists())

    def test_rejected_graphs_and_byte_caps_leave_committed_bytes_unchanged(self):
        first = self.ingest(); original = self.store.path.read_bytes()
        for graph in (replace(self.graph, project_sha256=digest('foreign')),
                      replace(self.graph, nodes=(replace(self.join.left, revision_sha256=digest('missing')), self.join.right))):
            with self.assertRaises(ValueError): self.ingest(graph=graph, before=first.cursor_after_sha256)
            self.assertEqual(self.store.path.read_bytes(), original)
        limited = SourceStore(self.root/'limited.json', **self.scope, max_bytes=1024)
        with self.assertRaises(ValueError): limited.ingest(self.source, self.records, snapshot_sha256=digest('raw'), graph=self.graph)
        self.assertFalse(limited.path.exists())

    def test_v3_journal_migration_preserves_hashes_and_waits_for_success(self):
        metadata = metadata_for(self.source, self.records[0])
        seed = self.store.ingest(self.source, self.records, snapshot_sha256=digest('old'), metadata=metadata)
        state = json.loads(self.store.path.read_text()); state['schema_version'] = 3
        for envelope in state['captures'][self.source.source_key].values(): envelope.pop('graph')
        self.store.path.write_text(json.dumps(state)); original = self.store.path.read_bytes()
        self.assertEqual(self.store.capture(self.source), metadata)
        self.assertIsNone(self.store.graph(self.source))
        self.assertEqual(self.store.path.read_bytes(), original)
        with self.assertRaises(ValueError): self.ingest(graph=replace(self.graph, scope_sha256=digest('foreign')), before=seed.cursor_after_sha256)
        self.assertEqual(self.store.path.read_bytes(), original)
        replay = self.store.ingest(self.source, self.records, cursor_before=seed.cursor_after_sha256,
            snapshot_sha256=digest('old'), metadata=metadata)
        self.assertEqual(replay.cursor_after_sha256, seed.cursor_after_sha256)
        self.assertEqual(self.store.status(self.source)['checkpoints'], 1)
        current = self.ingest(before=seed.cursor_after_sha256)
        migrated = json.loads(self.store.path.read_text())
        self.assertEqual(migrated['schema_version'], 4)
        self.assertEqual(migrated['records'], state['records'])
        self.assertEqual(migrated['cursors'][self.source.source_key][0], seed.cursor_after_sha256)
        self.assertEqual(self.store.capture(self.source, seed.cursor_after_sha256), metadata)
        self.assertIsNone(self.store.graph(self.source, seed.cursor_after_sha256))
        self.assertEqual(self.store.graph(self.source, current.cursor_after_sha256), self.graph)

    def test_modified_graph_cannot_relabel_a_committed_checkpoint(self):
        first = self.ingest(); state = json.loads(self.store.path.read_text())
        state['captures'][self.source.source_key][first.cursor_after_sha256]['graph']['joins'][0]['relation'] = 'supports'
        self.store.path.write_text(json.dumps(state)); malformed = self.store.path.read_bytes()
        with self.assertRaises(ValueError): self.store.status(self.source)
        self.assertEqual(self.store.path.read_bytes(), malformed)

    def test_late_revision_does_not_rebind_historical_graph_endpoints(self):
        first = self.ingest()
        revised = (self.records[0], replace(self.records[1], revision_sha256=digest('later')))
        newer = graph_for(self.source, revised)
        self.ingest(graph=newer, records=revised, before=first.cursor_after_sha256)
        self.assertEqual(self.store.graph(self.source, first.cursor_after_sha256), self.graph)
        self.assertEqual(self.store.graph(self.source), newer)
        self.assertNotEqual(self.graph.joins[0].right.revision_sha256, newer.joins[0].right.revision_sha256)

    def test_ambiguous_write_keeps_graph_and_records_in_the_same_commit(self):
        from agent_harness_lab.sources import store as module
        commit = module.atomic_json
        def uncertain(path, value):
            commit(path, value)
            raise OSError('synthetic directory sync failure')
        with patch.object(module, 'atomic_json', side_effect=uncertain):
            with self.assertRaises(CommitUncertain): self.ingest()
        self.assertEqual(self.store.status(self.source)['revisions'], 2)
        self.assertEqual(self.store.graph(self.source), self.graph)


class GitHubGraphTests(GitHubFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.selection = replace(self.selection, schema_version=5, details=('graph',))
        self.source = replace(self.source, version=self.selection.source_version, scope_sha256=self.selection.sha256)

    def test_v5_maps_all_relationships_to_exact_record_revisions_without_extra_requests(self):
        client, snapshot = self.read()
        self.assertEqual(len(snapshot.graph.nodes), len(snapshot.records))
        self.assertEqual(len(snapshot.graph.joins), len(client.summary['relations']))
        self.assertTrue(all(j.status == 'candidate' and j.basis == 'native_fields' for j in snapshot.graph.joins))
        self.assertIsNone(snapshot.graph.summary()['independent_roots'])
        self.assertEqual(len(self.targets), 14)
        revisions = {(r.source_sha256, r.native_id_sha256, r.revision_sha256) for r in snapshot.records}
        self.assertTrue(all(j.left.key in revisions and j.right.key in revisions for j in snapshot.graph.joins))

    def test_github_import_retains_prior_graph_after_review_revision_changes(self):
        scope = dict(project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256)
        registry = Registry(); registry.register(Capability('api', 'github', self.source.version,
            ('contextual_assertion', 'development_artifact')), GitHubAPI(self.base, self.selection))
        with tempfile.TemporaryDirectory() as directory:
            store = SourceStore(Path(directory)/'cache.json', **scope)
            first = registry.ingest(self.source, store, **scope); before = store.graph(self.source)
            self.review['body'] = 'SYNTHETIC_EDIT'
            second = registry.ingest(self.source, store, **scope)
            self.assertNotEqual(first.cursor_after_sha256, second.cursor_after_sha256)
            self.assertEqual(store.graph(self.source, first.cursor_after_sha256), before)
            self.assertNotEqual(store.graph(self.source), before)
            self.assertEqual(store.status(self.source)['evidence_graph']['statuses']['verified'], 0)
            self.assertNotIn('SYNTHETIC_', store.path.read_text())

    def test_older_versions_cannot_enable_graphs_and_remain_serializable(self):
        for version in (1, 2, 3, 4):
            with self.assertRaises(ValueError): replace(self.selection, schema_version=version)
            selection = replace(self.selection, schema_version=version, details=())
            self.assertEqual(GitHubSelection.parse(selection.to_dict()), selection)
            source = replace(self.source, version=selection.source_version, scope_sha256=selection.sha256)
            self.assertIsNone(GitHubAPI(self.base, selection).read(source).graph)
