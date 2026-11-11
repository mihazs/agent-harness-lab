"""Synthetic API rechecks; a claim or serialized report cannot certify itself."""
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import json
from unittest.mock import Mock

import pytest

from tests.support.github_case import GitHubPytestCase
from tests.unit.sources.test_source_github import Response

from agent_harness_lab.cli import main
from agent_harness_lab.sources.github_config import GitHubConfigured
from agent_harness_lab.sources.graph import EvidenceGraph, JoinReceipt
from agent_harness_lab.sources.registry import Capability, Registry
from agent_harness_lab.sources.resolve import recheck_github
from agent_harness_lab.sources.schema import digest
from agent_harness_lab.sources.store import SourceStore


class TestNativeRecheck(GitHubPytestCase):
    @pytest.fixture(autouse=True)
    def _recheck(self, _github, tmp_path):
        self.root = tmp_path
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
            assert secret not in output.getvalue()+error.getvalue()
        return code, json.loads(output.getvalue()) if output.getvalue() else None

    def test_default_probe_configured_probe_and_cached_status_never_connect(self):
        assert self.invoke('probe')[0] == 0
        assert self.targets == []
        self.source = replace(self.source, auth_mode='configured'); self.write_configuration()
        code, result = self.invoke('probe', '--authorization-file', str(self.credential))
        assert code == 0; assert not result['network_used']
        self.wire.assert_not_called()
        self.credential.write_text('Bearer SYNTHETIC_SECRET\n')
        assert self.invoke('import', '--authorization-file', str(self.credential))[0] == 0
        calls = len(self.targets); self.credential.unlink()
        code, result = self.invoke('status')
        assert code == 0; assert not result['source_read']; assert len(self.targets) == calls

    def test_public_preview_import_and_recheck_verify_exact_live_ids_without_writing_cache(self):
        code, preview = self.invoke('preview'); assert code == 0
        assert preview['network_used']; assert not self.store.path.exists()
        assert self.invoke('import')[0] == 0
        original = self.store.path.read_bytes()
        code, result = self.invoke('recheck'); assert code == 0
        assert result['statuses']['verified'] > 0
        assert result['statuses']['candidate'] > 0
        assert result['verification_scope'] == 'current_native_relationships_only'
        assert result['independent_roots'] is None; assert not result['task_outcomes_verified']
        assert not result['historical_interval_verified']; assert not result['execution_authorized']
        assert self.store.path.read_bytes() == original
        assert self.store.graph(self.source).summary()['statuses']['verified'] == 0
        files = [r for r in result['results'] if r['receipt']['relation'] == 'file_on']
        assert all(r['resolution_reason'] == 'native_identity_unverified' for r in files)

    def test_private_reads_require_explicit_credential_and_never_persist_it(self):
        self.source = replace(self.source, auth_mode='configured'); self.write_configuration()
        self.repo.update(private=True, visibility='private')
        assert self.invoke('preview')[0] == 3; self.wire.assert_not_called()
        self.credential.write_text('Bearer SYNTHETIC_SECRET\n')
        args = ('--authorization-file', str(self.credential))
        assert self.invoke('import', *args)[0] == 0
        assert self.invoke('recheck', *args)[0] == 0
        assert all(c.kwargs['headers']['Authorization'] == 'Bearer SYNTHETIC_SECRET' for c in self.connection.request.call_args_list)
        assert 'SYNTHETIC_SECRET' not in self.store.path.read_text()

    def test_scope_disabled_source_host_and_budget_are_checked_before_credentials_or_network(self):
        self.source = replace(self.source, auth_mode='configured'); self.write_configuration()
        assert self.invoke('preview', '--project-sha256', digest('foreign'))[0] == 2
        self.source = replace(self.source, enabled=False); self.write_configuration()
        assert self.invoke('preview')[0] == 3
        self.source = replace(self.source, enabled=True); self.write_configuration()
        for changed in ({**self.configuration, 'endpoint': 'https://evil.invalid/repos/owner/repo'},
                        {**self.configuration, 'limits': dict(max_pages=101)},
                        {**self.configuration, 'authorization': 'SYNTHETIC_SECRET'}):
            self.manifest.write_text(json.dumps(changed))
            assert self.invoke('preview', '--authorization-file', str(self.credential))[0] == 2
        self.wire.assert_not_called()

    def test_credential_links_oversize_and_control_characters_are_rejected_without_connection(self):
        self.source = replace(self.source, auth_mode='configured'); self.write_configuration()
        for contents in ('Bearer SYNTHETIC_SECRET\nInjected: value', 'x'*8193, '\xff'):
            self.credential.write_text(contents)
            assert self.invoke('preview', '--authorization-file', str(self.credential))[0] == 2
        linked = self.root/'linked'; linked.symlink_to(self.credential)
        assert self.invoke('preview', '--authorization-file', str(linked))[0] == 2
        self.wire.assert_not_called()

    def test_auth_file_cannot_be_read_for_an_unauthenticated_source(self):
        assert self.invoke('preview', '--authorization-file', str(self.credential))[0] == 2
        self.wire.assert_not_called()

    def test_changed_configuration_cannot_reuse_a_previously_admitted_source(self):
        connector = GitHubConfigured(self.manifest)
        source, _ = connector.declaration(**self.scope)
        self.configuration['selection']['issues'] = [9]
        self.manifest.write_text(json.dumps(self.configuration))
        with pytest.raises(ValueError): connector.read(source)
        self.wire.assert_not_called()

    def test_recheck_requires_a_captured_graph_and_builtin_connector(self):
        assert self.invoke('recheck')[0] == 3
        self.wire.assert_not_called()
        assert self.invoke('import')[0] == 0
        fake = Mock(); registry = Registry()
        registry.register(Capability('api', 'github', self.source.version, ('contextual_assertion', 'development_artifact')), fake)
        before = len(self.targets)
        with pytest.raises(NotImplementedError): registry.recheck(self.source, self.store, **self.scope)
        fake.read.assert_not_called(); assert len(self.targets) == before

    def test_reading_current_native_state_does_not_certify_an_old_head_revision(self):
        assert self.invoke('import')[0] == 0; original = self.store.path.read_bytes()
        self.pull['head']['sha'] = 'e'*40
        code, result = self.invoke('recheck'); assert code == 0
        stale = [r for r in result['results'] if r['resolution_reason'] == 'stale_revision']
        assert stale
        assert all(r['receipt']['status'] == 'unlinked' for r in stale)
        assert self.store.path.read_bytes() == original

    def test_stable_review_ids_do_not_certify_a_stale_review_revision(self):
        assert self.invoke('import')[0] == 0
        self.review['body'] = 'SYNTHETIC_REVISED_REVIEW'
        code, result = self.invoke('recheck'); assert code == 0
        reviews = [r for r in result['results'] if r['receipt']['relation'] in ('review_on', 'inline_review')]
        assert reviews
        assert all(r['receipt']['status'] == 'unlinked' and r['resolution_reason'] == 'stale_revision' for r in reviews)

    def test_recheck_preserves_configuration_journal_lock_and_directory_contents(self):
        assert self.invoke('import')[0] == 0
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        assert self.invoke('recheck')[0] == 0
        after = {p.name: p.read_bytes() for p in self.root.iterdir()}
        assert before == after, 'native recheck must not write configuration or cache files'

    def test_forged_relation_labels_and_conflicts_are_not_promoted_by_nearby_native_ids(self):
        assert self.invoke('import')[0] == 0
        proposed = self.store.graph(self.source)
        native = next(j for j in proposed.joins if j.relation == 'review_on')
        forged = replace(native, relation='same_execution', basis='heuristic')
        conflicted = replace(native, status='conflicted', reason='conflicting_evidence')
        graph = replace(proposed, joins=(forged, conflicted))
        result = recheck_github(self.source, GitHubConfigured(self.manifest), graph)
        assert result['statuses']['verified'] == 0
        assert result['statuses']['conflicted'] == 1
        assert result['statuses']['candidate'] == 1
        assert result['independent_roots'] is None

    def test_unvalidated_metadata_labels_are_not_echoed_in_resolution_receipts(self):
        assert self.invoke('import')[0] == 0
        proposed = self.store.graph(self.source)
        native = next(j for j in proposed.joins if j.relation == 'review_on')
        claim = replace(native, relation='same_execution', namespace='SYNTHETIC_SECRET', resolver_version='SYNTHETIC_SECRET')
        result = recheck_github(self.source, GitHubConfigured(self.manifest), replace(proposed, joins=(claim,)))
        assert 'SYNTHETIC_SECRET' not in json.dumps(result)
        assert result['results'][0]['claim_sha256'] == claim.sha256

    def test_validity_assertions_and_serialized_verified_receipts_cannot_relabel_the_cache(self):
        assert self.invoke('import')[0] == 0
        original = self.store.path.read_bytes(); proposed = self.store.graph(self.source)
        native = next(j for j in proposed.joins if j.relation == 'review_on')
        dated = replace(native, effective_from='2020-01-01T00:00:00Z', effective_until='2020-01-02T00:00:00Z')
        result = recheck_github(self.source, GitHubConfigured(self.manifest), replace(proposed, joins=(dated,)))
        receipt = JoinReceipt.parse(result['results'][0]['receipt'])
        assert receipt.status == 'verified'; assert receipt.effective_from is None; assert receipt.effective_until is None
        forged = replace(proposed, joins=(receipt,))
        with pytest.raises(ValueError):
            self.store.ingest(self.source, (), cursor_before=self.store.status(self.source)['cursor_sha256'],
                snapshot_sha256=digest('forged'), graph=forged)
        assert self.store.path.read_bytes() == original
        with pytest.raises(ValueError): EvidenceGraph.parse(result)

    def test_cross_reference_without_native_occurrence_identity_remains_candidate(self):
        self.selection = replace(self.selection, details=('graph', 'timeline'))
        self.source = replace(self.source, scope_sha256=self.selection.sha256)
        self.scope['scope_sha256'] = self.source.scope_sha256; self.write_configuration()
        cross = dict(event='cross-referenced', created_at=self.stamp, updated_at=self.stamp, actor=self.actor,
            source=dict(type='issue', issue=dict(id=80, number=8, url=self.base+'/issues/8')))
        self.routes['/issues/7/timeline?per_page=100&page=1'] = [cross]
        self.routes['/issues/8/timeline?per_page=100&page=1'] = []
        assert self.invoke('import')[0] == 0
        code, result = self.invoke('recheck'); assert code == 0
        matches = [r for r in result['results'] if r['receipt']['relation'] == 'references_issue']
        assert len(matches) == 1; assert matches[0]['receipt']['status'] == 'candidate'
        assert matches[0]['resolution_reason'] == 'native_identity_unverified'

    def test_rate_limited_import_is_blocked_access_with_receipt_and_reset_and_no_checkpoint(self, tmp_path, monkeypatch):
        monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'one'))
        assert self.invoke('import')[0] == 0; original = self.store.path.read_bytes()
        self.routes[''] = Response(dict(message='rate limit'), 403, {'X-RateLimit-Remaining': '0', 'X-RateLimit-Reset': '4102444800'})
        code, result = self.invoke('import')
        assert code == 3
        assert (result['status'], result['checkpoint_committed']) == ('blocked', False)
        assert (result['rate_limit']['halt_reason'], result['rate_limit']['reset_epoch']) == ('primary_limit', 4102444800)
        assert self.store.path.read_bytes() == original
        # A later run sees the shared cooldown; a permission 403 on a fresh state is a denial, not a limit.
        assert self.invoke('import')[0] == 3
        monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'two'))
        self.routes[''] = Response(b'SYNTHETIC_SECRET', 403, {'X-RateLimit-Remaining': '7'})
        assert self.invoke('import')[0] == 2

    def test_http_denial_and_foreign_repository_never_advance_checkpoint_or_emit_proofs(self, tmp_path, monkeypatch):
        monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path))
        assert self.invoke('import')[0] == 0; original = self.store.path.read_bytes()
        for status in (403, 404, 429):
            self.routes[''] = Response(b'SYNTHETIC_SECRET', status)
            code, result = self.invoke('recheck')
            assert code == 2; assert result is None
            assert self.store.path.read_bytes() == original
        self.routes[''] = self.repo; self.repo['id'] = 999
        assert self.invoke('recheck')[0] == 2
        assert self.store.path.read_bytes() == original
