"""Synthetic native Claude checkpoints preserve source claims and exact revisions."""
from copy import deepcopy
from dataclasses import replace
import io
import json
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

import pytest

from tests.integration.telemetry import test_claude_native as fixtures
from agent_harness_lab.cli import main
from agent_harness_lab.sources.schema import SourceSpec, digest
from agent_harness_lab.sources.store import SourceStore
from agent_harness_lab.sources.resolve import recheck
from agent_harness_lab.sources.snapshot import LocalExport


class ClaudeSourceTests:
    """Synthetic Claude source declaration; test_source_claude_root builds it through setUp() and doCleanups()."""
    def setUp(self, root=None):
        self.fixture = fixtures.ClaudeNativeTests(); self.fixture.setUp(root)
        self.root, self.project, self.capture = self.fixture.root, self.fixture.project, self.fixture.path
        self.rows = deepcopy(self.fixture.rows)
        self.manifest, self.descriptor = self.root/'source.json', self.root/'selection.json'
        self.cache = self.root/'private/journal.json'
        self.selected = dict(schema_version=1, project='project', capture='session.jsonl', max_records=10000)
        self.declare()

    def doCleanups(self): self.fixture.doCleanups()

    def declare(self):
        self.source = SourceSpec('local_file', 'claude-jsonl', '2.1.316-v1', digest(str(self.root.resolve())),
            digest('synthetic-project'), digest(self.selected), identity_provenance='source_declared_id', enabled=True)
        self.descriptor.write_text(json.dumps(self.selected))
        self.manifest.write_text(json.dumps(dict(schema_version=1, source=self.source.to_dict(), data='selection.json')))
        self.scope = dict(project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256)

    def write(self): self.capture.write_text(''.join(json.dumps(r)+'\n' for r in self.rows))

    def store(self): return SourceStore(self.cache, **self.scope)

    def invoke(self, operation, *extra):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(['sources', operation, '--manifest', str(self.manifest), '--project-sha256', self.source.project_sha256,
                '--scope-sha256', self.source.scope_sha256, '--cache', str(self.cache), *extra])
        for private in ('SYNTHETIC_SECRET', str(self.root), 'synthetic-thread'):
            assert private not in out.getvalue()+err.getvalue()
        return code, json.loads(out.getvalue()) if out.getvalue() else None

    def reader(self):
        from agent_harness_lab.sources.claude import ClaudeCapture
        return ClaudeCapture(self.manifest)

    def snapshot(self): return self.reader().read(self.source)


class TestClaudeSource(ClaudeSourceTests):
    @pytest.fixture(autouse=True)
    def _source(self, tmp_path):
        self.setUp(tmp_path)
        yield
        self.doCleanups()

    def test_native_source_file_does_not_inherit_git_total_read_limit(self):
        with patch('agent_harness_lab.sources.git_view.MAX_BYTES', 10):
            assert self.snapshot().records

    def test_unlimited_source_keeps_more_than_the_former_normalized_record_ceiling(self):
        self.rows = [self.rows[1]]
        self.rows[0]['message']['content'] = [dict(type='text', text='synthetic')]*10001
        self.write(); self.selected.pop('max_records'); self.declare()
        assert len(self.snapshot().records) == 10001

    def test_native_cli_preview_import_status_recheck_and_privacy_use_shared_journal(self):
        code, preview = self.invoke('preview'); assert code == 0
        assert preview['checkpoint_ready']; assert preview['records'] == 12
        assert preview['evidence_graph']['joins'] == 9; assert not self.cache.exists()
        assert self.invoke('import')[1]['imported'] == 12
        status = self.invoke('status')[1]; assert status['execution_metadata']['identities'] == 8
        before = self.cache.read_bytes(); code, report = self.invoke('recheck')
        assert code == 0; assert report['statuses']['verified'] == 9
        assert not report['network_used']; assert report['independent_roots'] is None
        assert self.cache.read_bytes() == before; assert self.cache.stat().st_mode & 0o777 == 0o600
        assert 'synthetic-session' not in self.cache.read_text(); assert 'SYNTHETIC_SECRET' not in self.cache.read_text()
        self.capture.unlink(); assert self.invoke('status')[1]['revisions'] == 12

    def test_probe_scope_disabled_and_unknown_profile_do_not_read_capture(self):
        with patch('agent_harness_lab.sources.claude.ClaudeCapture.read', side_effect=AssertionError('no native read')):
            assert self.invoke('probe')[0] == 0
            assert self.invoke('preview', '--project-sha256', digest('foreign'))[0] == 2
            for changed in (replace(self.source, enabled=False), replace(self.source, version='future')):
                self.manifest.write_text(json.dumps(dict(schema_version=1, source=changed.to_dict(), data='selection.json')))
                assert self.invoke('preview')[0] == 3

    def test_native_session_runtime_and_model_reports_stay_declared_with_effective_bindings_unknown(self):
        snapshot = self.snapshot(); fields = snapshot.metadata.summary()['fields']
        assert fields['session']['declared'] == 8; assert fields['served_model']['declared'] == 3
        assert all(field.status == 'declared' for identity in snapshot.metadata.identities for field in identity.fields)
        for name in ('requested_model', 'effective_harness', 'effective_config', 'memory_snapshot', 'parent_session', 'task_root'):
            assert fields[name]['unknown'] == 8
        assert {field.namespace for i in snapshot.metadata.identities for field in i.fields if field.name == 'served_model'} == {'claude.reported_model'}
        assert {field.namespace for i in snapshot.metadata.identities for field in i.fields if field.name == 'session'} == {'claude.session'}
        assert snapshot.graph.summary()['independent_roots'] is None
        (self.project/'CLAUDE.md').write_text('SYNTHETIC_CHANGED_CONFIG')
        assert self.snapshot().metadata == snapshot.metadata

    def test_reimport_is_idempotent_and_current_recheck_does_not_promote_stored_candidates(self):
        assert self.invoke('import')[0] == 0; before = self.cache.read_bytes()
        assert self.invoke('import')[1]['imported'] == 0; assert self.cache.read_bytes() == before
        assert self.invoke('recheck')[1]['statuses']['verified'] == 9
        assert self.cache.read_bytes() == before
        assert self.invoke('status')[1]['evidence_graph']['statuses']['verified'] == 0

    def test_body_edit_adds_exact_native_revision_without_retriggering_metadata_monitor(self):
        before_monitor = self.fixture.collect()['fingerprint']; assert self.invoke('import')[0] == 0
        store = self.store(); cursor = store.status(self.source)['cursor_sha256']; old = store.capture(self.source, cursor)
        call = next(r for r in self.snapshot().records if r.record_type == 'claude_tool_call')
        self.rows[2]['message']['content'][0]['input']['command'] = 'SYNTHETIC_CHANGED_BODY'; self.write()
        assert self.invoke('import')[1]['imported'] == 1
        assert len(store.history(self.source, call.native_id_sha256)) == 2
        assert store.capture(self.source, cursor) == old
        assert self.fixture.collect()['fingerprint'] == before_monitor
        assert 'SYNTHETIC_CHANGED_BODY' not in self.cache.read_text()

    def test_late_model_report_preserves_prior_identity_and_checkpoint_graph(self):
        assert self.invoke('import')[0] == 0; store = self.store(); cursor = store.status(self.source)['cursor_sha256']
        old, graph = store.capture(self.source, cursor), store.graph(self.source, cursor)
        self.rows.append(self.fixture.row('assistant', 'late', 'answer', dict(role='assistant', id='new-response', model='SYNTHETIC_NEW_MODEL',
            content='SYNTHETIC_SECRET', usage=dict(input_tokens=1, output_tokens=1), stop_reason='end_turn')))
        self.write(); assert self.invoke('import')[1]['imported'] == 3
        assert store.capture(self.source, cursor) == old; assert store.graph(self.source, cursor) == graph
        assert old.summary()['fields']['served_model']['declared'] == 3
        assert store.capture(self.source).summary()['fields']['served_model']['declared'] == 4

    def test_partial_tail_preview_cannot_create_or_advance_checkpoint(self):
        full = self.capture.read_bytes(); self.capture.write_bytes(full.rstrip(b'\n'))
        assert not self.invoke('preview')[1]['checkpoint_ready']
        assert self.invoke('import')[0] == 2; assert not self.cache.parent.exists()
        self.capture.write_bytes(full); assert self.invoke('import')[0] == 0; before = self.cache.read_bytes()
        self.capture.write_bytes(full+b'{"type":')
        assert self.invoke('import')[0] == 2; assert self.invoke('recheck')[0] == 2
        assert self.cache.read_bytes() == before

    def test_raw_duplicate_uuid_conflict_is_not_hidden_by_ordinary_body_insensitive_dedup(self):
        baseline = self.fixture.collect()['fingerprint']; assert self.invoke('import')[0] == 0; before = self.cache.read_bytes()
        other = deepcopy(self.rows[2]); other['message']['content'][0]['input']['command'] = 'SYNTHETIC_CHANGED_BODY'
        self.rows.append(other); self.write()
        assert self.fixture.collect()['fingerprint'] == baseline
        assert self.invoke('import')[0] == 2; assert self.cache.read_bytes() == before

    def test_graph_excludes_derived_facets_and_keeps_missing_message_parents_unlinked(self):
        self.rows[1]['parentUuid'] = 'outside-capture'; self.write(); snapshot = self.snapshot()
        native = {r.native_id_sha256 for r in snapshot.records if r.provenance == 'native_field'}
        derived = {r.native_id_sha256 for r in snapshot.records if r.provenance == 'unknown'}
        assert len(derived) == 4
        for join in snapshot.graph.joins:
            assert join.left.native_id_sha256 in native
            if join.right is not None: assert join.right.native_id_sha256 in native
        assert sum(j.status == 'unlinked' for j in snapshot.graph.joins) == 1
        assert not any(r.record_type == 'claude_session_start' for r in snapshot.records)
        report = recheck(self.source, self.reader(), snapshot.graph)
        assert report['statuses']['unlinked'] == 1; assert not report['task_outcomes_verified']

    def test_self_parent_pointer_keeps_one_evidence_ref_without_inventing_lineage(self):
        self.rows[1]['parentUuid'] = 'u1'; self.write(); snapshot = self.snapshot()
        reflexive = next(j for j in snapshot.graph.joins if j.relation == 'native_message_parent' and j.left == j.right)
        assert len(reflexive.evidence) == 1; assert reflexive.status == 'candidate'
        assert self.invoke('import')[0] == 0; before = self.cache.read_bytes()
        report = self.invoke('recheck')[1]
        assert report['independent_roots'] is None; assert not report['task_outcomes_verified']
        assert not report['historical_interval_verified']; assert self.cache.read_bytes() == before

    def test_multi_block_message_has_one_identity_and_revision_bound_membership(self):
        self.rows[2]['message']['content'].append(dict(type='text', text='SYNTHETIC_SECRET')); self.write()
        snapshot = self.snapshot(); assert len(snapshot.metadata.identities) == 8
        assert sum(j.relation == 'native_message_member' for j in snapshot.graph.joins) == 1
        assert self.invoke('import')[0] == 0; before = self.cache.read_bytes()
        assert self.invoke('recheck')[1]['statuses']['verified'] == 10; assert self.cache.read_bytes() == before

    def test_stale_raw_revisions_and_forged_relationship_inputs_never_verify(self):
        graph = self.snapshot().graph; assert self.invoke('import')[0] == 0; before = self.cache.read_bytes()
        self.rows[2]['message']['content'][0]['input']['command'] = 'SYNTHETIC_CHANGED_BODY'; self.write()
        report = self.invoke('recheck')[1]; assert report['statuses']['unlinked'] > 0
        assert self.cache.read_bytes() == before
        class Forged(type(self.reader())):
            def read(self, source): raise AssertionError('never execute claimed proof reader')
        for connector in (LocalExport(self.manifest), Forged(self.manifest)):
            with pytest.raises(NotImplementedError): recheck(self.source, connector, graph)
        with patch('agent_harness_lab.sources.claude.ClaudeCapture.read', side_effect=AssertionError('no native read')):
            for proposed in (replace(graph, project_sha256=digest('foreign')),
                    replace(graph, joins=(replace(graph.joins[0], status='verified', reason='native_proof'),))):
                with pytest.raises(ValueError): recheck(self.source, self.reader(), proposed)

    def test_invalid_native_version_link_and_selection_reject_without_changing_journal(self):
        assert self.invoke('import')[0] == 0; before = self.cache.read_bytes(); full = self.capture.read_bytes()
        self.rows[2]['version'] = 'future'; self.write(); assert self.invoke('import')[0] == 2
        self.capture.write_bytes(full); moved = self.root/'other.jsonl'; self.capture.rename(moved); self.capture.symlink_to(moved)
        assert self.invoke('import')[0] == 2; assert self.cache.read_bytes() == before
        self.capture.unlink(); moved.rename(self.capture)
        self.selected['capture'] = '../outside'; self.declare(); assert self.invoke('preview')[0] == 2
        assert self.cache.read_bytes() == before

    def test_conversion_selection_and_input_validation_share_one_deadline(self):
        from agent_harness_lab.sources.native import EvidenceRecord, relationships
        clock = [0]
        def delayed(*args, **kwargs):
            result = EvidenceRecord(*args, **kwargs); clock[0] = 11; return result
        with patch('agent_harness_lab.sources.git_view.time.monotonic', side_effect=lambda: clock[0]), \
             patch('agent_harness_lab.sources.native.EvidenceRecord', delayed): assert self.snapshot().records
        def mutate(*args, **kwargs):
            result = relationships(*args, **kwargs); self.descriptor.write_text('{}'); return result
        with patch('agent_harness_lab.sources.native.relationships', mutate): assert self.invoke('import')[0] == 2
        assert not self.cache.exists()
