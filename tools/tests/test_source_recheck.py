"""Synthetic API rechecks; a claim or serialized report cannot certify itself."""
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

if __package__:
    from .test_source_github import GitHubFixture, Response
else:
    from test_source_github import GitHubFixture, Response

from agent_harness_lab.cli import main
from agent_harness_lab.sources.github_config import GitHubConfigured
from agent_harness_lab.sources.graph import EvidenceGraph, JoinReceipt
from agent_harness_lab.sources.registry import Capability, Registry
from agent_harness_lab.sources.resolve import recheck_github
from agent_harness_lab.sources.schema import digest
from agent_harness_lab.sources.store import SourceStore


class NativeRecheckTests(GitHubFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.selection = replace(self.selection, schema_version=5, details=('graph',))
        self.source = replace(self.source, version=self.selection.source_version, scope_sha256=self.selection.sha256)
        self.scope = dict(project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256)
        self.manifest, self.credential = self.root/'source.json', self.root/'authorization'
        self.store = SourceStore(self.root/'journal.json', **self.scope)
        self.write_configuration()

    def write_configuration(self):
        self.configuration = dict(schema_version=1, source=self.source.to_dict(), endpoint=self.base,
            selection=self.selection.to_dict(), limits=dict(max_pages=40))
        self.manifest.write_text(json.dumps(self.configuration))

    def invoke(self, operation, *extra):
        output, error = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            code = main(['sources', operation, '--manifest', str(self.manifest), '--cache', str(self.store.path),
                '--project-sha256', self.source.project_sha256, '--scope-sha256', self.source.scope_sha256, *extra])
        for secret in ('SYNTHETIC_SECRET', 'SYNTHETIC_PRIVATE', str(self.root), self.base):
            self.assertNotIn(secret, output.getvalue()+error.getvalue())
        return code, json.loads(output.getvalue()) if output.getvalue() else None

    def test_default_probe_configured_probe_and_cached_status_never_connect(self):
        self.assertEqual(self.invoke('probe')[0], 0)
        self.assertEqual(self.targets, [])
        self.source = replace(self.source, auth_mode='configured'); self.write_configuration()
        code, result = self.invoke('probe', '--authorization-file', str(self.credential))
        self.assertEqual(code, 0); self.assertFalse(result['network_used'])
        self.wire.assert_not_called()
        self.credential.write_text('Bearer SYNTHETIC_SECRET\n')
        self.assertEqual(self.invoke('import', '--authorization-file', str(self.credential))[0], 0)
        calls = len(self.targets); self.credential.unlink()
        code, result = self.invoke('status')
        self.assertEqual(code, 0); self.assertFalse(result['source_read']); self.assertEqual(len(self.targets), calls)

    def test_public_preview_import_and_recheck_verify_exact_live_ids_without_writing_cache(self):
        code, preview = self.invoke('preview'); self.assertEqual(code, 0)
        self.assertTrue(preview['network_used']); self.assertFalse(self.store.path.exists())
        self.assertEqual(self.invoke('import')[0], 0)
        original = self.store.path.read_bytes()
        code, result = self.invoke('recheck'); self.assertEqual(code, 0)
        self.assertGreater(result['statuses']['verified'], 0)
        self.assertGreater(result['statuses']['candidate'], 0)
        self.assertEqual(result['verification_scope'], 'current_native_relationships_only')
        self.assertIsNone(result['independent_roots']); self.assertFalse(result['task_outcomes_verified'])
        self.assertFalse(result['historical_interval_verified']); self.assertFalse(result['execution_authorized'])
        self.assertEqual(self.store.path.read_bytes(), original)
        self.assertEqual(self.store.graph(self.source).summary()['statuses']['verified'], 0)
        files = [r for r in result['results'] if r['receipt']['relation'] == 'file_on']
        self.assertTrue(all(r['resolution_reason'] == 'native_identity_unverified' for r in files))

    def test_private_reads_require_explicit_credential_and_never_persist_it(self):
        self.source = replace(self.source, auth_mode='configured'); self.write_configuration()
        self.repo.update(private=True, visibility='private')
        self.assertEqual(self.invoke('preview')[0], 3); self.wire.assert_not_called()
        self.credential.write_text('Bearer SYNTHETIC_SECRET\n')
        args = ('--authorization-file', str(self.credential))
        self.assertEqual(self.invoke('import', *args)[0], 0)
        self.assertEqual(self.invoke('recheck', *args)[0], 0)
        self.assertTrue(all(c.kwargs['headers']['Authorization'] == 'Bearer SYNTHETIC_SECRET' for c in self.connection.request.call_args_list))
        self.assertNotIn('SYNTHETIC_SECRET', self.store.path.read_text())

    def test_scope_disabled_source_host_and_budget_are_checked_before_credentials_or_network(self):
        self.source = replace(self.source, auth_mode='configured'); self.write_configuration()
        self.assertEqual(self.invoke('preview', '--project-sha256', digest('foreign'))[0], 2)
        self.source = replace(self.source, enabled=False); self.write_configuration()
        self.assertEqual(self.invoke('preview')[0], 3)
        self.source = replace(self.source, enabled=True); self.write_configuration()
        for changed in ({**self.configuration, 'endpoint': 'https://evil.invalid/repos/owner/repo'},
                        {**self.configuration, 'limits': dict(max_pages=101)},
                        {**self.configuration, 'authorization': 'SYNTHETIC_SECRET'}):
            self.manifest.write_text(json.dumps(changed))
            self.assertEqual(self.invoke('preview', '--authorization-file', str(self.credential))[0], 2)
        self.wire.assert_not_called()

    def test_credential_links_oversize_and_control_characters_are_rejected_without_connection(self):
        self.source = replace(self.source, auth_mode='configured'); self.write_configuration()
        for contents in ('Bearer SYNTHETIC_SECRET\nInjected: value', 'x'*8193, '\xff'):
            self.credential.write_text(contents)
            self.assertEqual(self.invoke('preview', '--authorization-file', str(self.credential))[0], 2)
        linked = self.root/'linked'; linked.symlink_to(self.credential)
        self.assertEqual(self.invoke('preview', '--authorization-file', str(linked))[0], 2)
        self.wire.assert_not_called()

    def test_auth_file_cannot_be_read_for_an_unauthenticated_source(self):
        self.assertEqual(self.invoke('preview', '--authorization-file', str(self.credential))[0], 2)
        self.wire.assert_not_called()

    def test_changed_configuration_cannot_reuse_a_previously_admitted_source(self):
        connector = GitHubConfigured(self.manifest)
        source, _ = connector.declaration(**self.scope)
        self.configuration['selection']['issues'] = [9]
        self.manifest.write_text(json.dumps(self.configuration))
        with self.assertRaises(ValueError): connector.read(source)
        self.wire.assert_not_called()

    def test_recheck_requires_a_captured_graph_and_builtin_connector(self):
        self.assertEqual(self.invoke('recheck')[0], 3)
        self.wire.assert_not_called()
        self.assertEqual(self.invoke('import')[0], 0)
        fake = Mock(); registry = Registry()
        registry.register(Capability('api', 'github', self.source.version, ('contextual_assertion', 'development_artifact')), fake)
        before = len(self.targets)
        with self.assertRaises(NotImplementedError): registry.recheck(self.source, self.store, **self.scope)
        fake.read.assert_not_called(); self.assertEqual(len(self.targets), before)

    def test_reading_current_native_state_does_not_certify_an_old_head_revision(self):
        self.assertEqual(self.invoke('import')[0], 0); original = self.store.path.read_bytes()
        self.pull['head']['sha'] = 'e'*40
        code, result = self.invoke('recheck'); self.assertEqual(code, 0)
        stale = [r for r in result['results'] if r['resolution_reason'] == 'stale_revision']
        self.assertTrue(stale)
        self.assertTrue(all(r['receipt']['status'] == 'unlinked' for r in stale))
        self.assertEqual(self.store.path.read_bytes(), original)

    def test_stable_review_ids_do_not_certify_a_stale_review_revision(self):
        self.assertEqual(self.invoke('import')[0], 0)
        self.review['body'] = 'SYNTHETIC_REVISED_REVIEW'
        code, result = self.invoke('recheck'); self.assertEqual(code, 0)
        reviews = [r for r in result['results'] if r['receipt']['relation'] in ('review_on', 'inline_review')]
        self.assertTrue(reviews)
        self.assertTrue(all(r['receipt']['status'] == 'unlinked' and r['resolution_reason'] == 'stale_revision' for r in reviews))

    def test_recheck_preserves_configuration_journal_lock_and_directory_contents(self):
        self.assertEqual(self.invoke('import')[0], 0)
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        self.assertEqual(self.invoke('recheck')[0], 0)
        after = {p.name: p.read_bytes() for p in self.root.iterdir()}
        self.assertTrue(before == after, 'native recheck must not write configuration or cache files')

    def test_forged_relation_labels_and_conflicts_are_not_promoted_by_nearby_native_ids(self):
        self.assertEqual(self.invoke('import')[0], 0)
        proposed = self.store.graph(self.source)
        native = next(j for j in proposed.joins if j.relation == 'review_on')
        forged = replace(native, relation='same_execution', basis='heuristic')
        conflicted = replace(native, status='conflicted', reason='conflicting_evidence')
        graph = replace(proposed, joins=(forged, conflicted))
        result = recheck_github(self.source, GitHubConfigured(self.manifest), graph)
        self.assertEqual(result['statuses']['verified'], 0)
        self.assertEqual(result['statuses']['conflicted'], 1)
        self.assertEqual(result['statuses']['candidate'], 1)
        self.assertIsNone(result['independent_roots'])

    def test_unvalidated_metadata_labels_are_not_echoed_in_resolution_receipts(self):
        self.assertEqual(self.invoke('import')[0], 0)
        proposed = self.store.graph(self.source)
        native = next(j for j in proposed.joins if j.relation == 'review_on')
        claim = replace(native, relation='same_execution', namespace='SYNTHETIC_SECRET', resolver_version='SYNTHETIC_SECRET')
        result = recheck_github(self.source, GitHubConfigured(self.manifest), replace(proposed, joins=(claim,)))
        self.assertNotIn('SYNTHETIC_SECRET', json.dumps(result))
        self.assertEqual(result['results'][0]['claim_sha256'], claim.sha256)

    def test_validity_assertions_and_serialized_verified_receipts_cannot_relabel_the_cache(self):
        self.assertEqual(self.invoke('import')[0], 0)
        original = self.store.path.read_bytes(); proposed = self.store.graph(self.source)
        native = next(j for j in proposed.joins if j.relation == 'review_on')
        dated = replace(native, effective_from='2020-01-01T00:00:00Z', effective_until='2020-01-02T00:00:00Z')
        result = recheck_github(self.source, GitHubConfigured(self.manifest), replace(proposed, joins=(dated,)))
        receipt = JoinReceipt.parse(result['results'][0]['receipt'])
        self.assertEqual(receipt.status, 'verified'); self.assertIsNone(receipt.effective_from); self.assertIsNone(receipt.effective_until)
        forged = replace(proposed, joins=(receipt,))
        with self.assertRaises(ValueError):
            self.store.ingest(self.source, (), cursor_before=self.store.status(self.source)['cursor_sha256'],
                snapshot_sha256=digest('forged'), graph=forged)
        self.assertEqual(self.store.path.read_bytes(), original)
        with self.assertRaises(ValueError): EvidenceGraph.parse(result)

    def test_cross_reference_without_native_occurrence_identity_remains_candidate(self):
        self.selection = replace(self.selection, details=('graph', 'timeline'))
        self.source = replace(self.source, scope_sha256=self.selection.sha256)
        self.scope['scope_sha256'] = self.source.scope_sha256; self.write_configuration()
        cross = dict(event='cross-referenced', created_at=self.stamp, updated_at=self.stamp, actor=self.actor,
            source=dict(type='issue', issue=dict(id=80, number=8, url=self.base+'/issues/8')))
        self.routes['/issues/7/timeline?per_page=100&page=1'] = [cross]
        self.routes['/issues/8/timeline?per_page=100&page=1'] = []
        self.assertEqual(self.invoke('import')[0], 0)
        code, result = self.invoke('recheck'); self.assertEqual(code, 0)
        matches = [r for r in result['results'] if r['receipt']['relation'] == 'references_issue']
        self.assertEqual(len(matches), 1); self.assertEqual(matches[0]['receipt']['status'], 'candidate')
        self.assertEqual(matches[0]['resolution_reason'], 'native_identity_unverified')

    def test_http_denial_and_foreign_repository_never_advance_checkpoint_or_emit_proofs(self):
        self.assertEqual(self.invoke('import')[0], 0); original = self.store.path.read_bytes()
        for status in (403, 404, 429):
            self.routes[''] = Response(b'SYNTHETIC_SECRET', status)
            code, result = self.invoke('recheck')
            self.assertEqual(code, 2); self.assertIsNone(result)
            self.assertEqual(self.store.path.read_bytes(), original)
        self.routes[''] = self.repo; self.repo['id'] = 999
        self.assertEqual(self.invoke('recheck')[0], 2)
        self.assertEqual(self.store.path.read_bytes(), original)
