"""Synthetic native Claude checkpoints preserve source claims and exact revisions."""
from copy import deepcopy
from dataclasses import replace
import json
import unittest
from unittest.mock import patch
if __package__:
    from . import test_claude_native as fixtures
    from . import test_source_codex as journal
else:
    import test_claude_native as fixtures
    import test_source_codex as journal
from agent_harness_lab.sources.schema import SourceSpec, digest
from agent_harness_lab.sources.resolve import recheck
from agent_harness_lab.sources.snapshot import LocalExport


class ClaudeSourceTests(unittest.TestCase):
    invoke = journal.NativeSourceTests.invoke
    store = journal.NativeSourceTests.store
    write = journal.NativeSourceTests.write

    def setUp(self):
        self.fixture = fixtures.ClaudeNativeTests(); self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.root, self.project, self.capture = self.fixture.root, self.fixture.project, self.fixture.path
        self.rows = deepcopy(self.fixture.rows)
        self.manifest, self.descriptor = self.root/'source.json', self.root/'selection.json'
        self.cache = self.root/'private/journal.json'
        self.selected = dict(schema_version=1, project='project', capture='session.jsonl', max_records=10000)
        self.declare()

    def declare(self):
        self.source = SourceSpec('local_file', 'claude-jsonl', '2.1.316-v1', digest(str(self.root.resolve())),
            digest('synthetic-project'), digest(self.selected), identity_provenance='source_declared_id', enabled=True)
        self.descriptor.write_text(json.dumps(self.selected))
        self.manifest.write_text(json.dumps(dict(schema_version=1, source=self.source.to_dict(), data='selection.json')))
        self.scope = dict(project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256)

    def reader(self):
        from agent_harness_lab.sources.claude import ClaudeCapture
        return ClaudeCapture(self.manifest)

    def snapshot(self): return self.reader().read(self.source)

    def test_native_cli_preview_import_status_recheck_and_privacy_use_shared_journal(self):
        code, preview = self.invoke('preview'); self.assertEqual(code, 0)
        self.assertTrue(preview['checkpoint_ready']); self.assertEqual(preview['records'], 12)
        self.assertEqual(preview['evidence_graph']['joins'], 9); self.assertFalse(self.cache.exists())
        self.assertEqual(self.invoke('import')[1]['imported'], 12)
        status = self.invoke('status')[1]; self.assertEqual(status['execution_metadata']['identities'], 8)
        before = self.cache.read_bytes(); code, report = self.invoke('recheck')
        self.assertEqual(code, 0); self.assertEqual(report['statuses']['verified'], 9)
        self.assertFalse(report['network_used']); self.assertIsNone(report['independent_roots'])
        self.assertEqual(self.cache.read_bytes(), before); self.assertEqual(self.cache.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('synthetic-session', self.cache.read_text()); self.assertNotIn('SYNTHETIC_SECRET', self.cache.read_text())
        self.capture.unlink(); self.assertEqual(self.invoke('status')[1]['revisions'], 12)

    def test_probe_scope_disabled_and_unknown_profile_do_not_read_capture(self):
        with patch('agent_harness_lab.sources.claude.ClaudeCapture.read', side_effect=AssertionError('no native read')):
            self.assertEqual(self.invoke('probe')[0], 0)
            self.assertEqual(self.invoke('preview', '--project-sha256', digest('foreign'))[0], 2)
            for changed in (replace(self.source, enabled=False), replace(self.source, version='future')):
                self.manifest.write_text(json.dumps(dict(schema_version=1, source=changed.to_dict(), data='selection.json')))
                self.assertEqual(self.invoke('preview')[0], 3)

    def test_native_session_runtime_and_model_reports_stay_declared_with_effective_bindings_unknown(self):
        snapshot = self.snapshot(); fields = snapshot.metadata.summary()['fields']
        self.assertEqual(fields['session']['declared'], 8); self.assertEqual(fields['served_model']['declared'], 3)
        self.assertTrue(all(field.status == 'declared' for identity in snapshot.metadata.identities for field in identity.fields))
        for name in ('requested_model', 'effective_harness', 'effective_config', 'memory_snapshot', 'parent_session', 'task_root'):
            self.assertEqual(fields[name]['unknown'], 8)
        self.assertEqual({field.namespace for i in snapshot.metadata.identities for field in i.fields if field.name == 'served_model'}, {'claude.reported_model'})
        self.assertEqual({field.namespace for i in snapshot.metadata.identities for field in i.fields if field.name == 'session'}, {'claude.session'})
        self.assertIsNone(snapshot.graph.summary()['independent_roots'])
        (self.project/'CLAUDE.md').write_text('SYNTHETIC_CHANGED_CONFIG')
        self.assertEqual(self.snapshot().metadata, snapshot.metadata)

    def test_reimport_is_idempotent_and_current_recheck_does_not_promote_stored_candidates(self):
        self.assertEqual(self.invoke('import')[0], 0); before = self.cache.read_bytes()
        self.assertEqual(self.invoke('import')[1]['imported'], 0); self.assertEqual(self.cache.read_bytes(), before)
        self.assertEqual(self.invoke('recheck')[1]['statuses']['verified'], 9)
        self.assertEqual(self.cache.read_bytes(), before)
        self.assertEqual(self.invoke('status')[1]['evidence_graph']['statuses']['verified'], 0)

    def test_body_edit_adds_exact_native_revision_without_retriggering_metadata_monitor(self):
        before_monitor = self.fixture.collect()['fingerprint']; self.assertEqual(self.invoke('import')[0], 0)
        store = self.store(); cursor = store.status(self.source)['cursor_sha256']; old = store.capture(self.source, cursor)
        call = next(r for r in self.snapshot().records if r.record_type == 'claude_tool_call')
        self.rows[2]['message']['content'][0]['input']['command'] = 'SYNTHETIC_CHANGED_BODY'; self.write()
        self.assertEqual(self.invoke('import')[1]['imported'], 1)
        self.assertEqual(len(store.history(self.source, call.native_id_sha256)), 2)
        self.assertEqual(store.capture(self.source, cursor), old)
        self.assertEqual(self.fixture.collect()['fingerprint'], before_monitor)
        self.assertNotIn('SYNTHETIC_CHANGED_BODY', self.cache.read_text())

    def test_late_model_report_preserves_prior_identity_and_checkpoint_graph(self):
        self.assertEqual(self.invoke('import')[0], 0); store = self.store(); cursor = store.status(self.source)['cursor_sha256']
        old, graph = store.capture(self.source, cursor), store.graph(self.source, cursor)
        self.rows.append(self.fixture.row('assistant', 'late', 'answer', dict(role='assistant', id='new-response', model='SYNTHETIC_NEW_MODEL',
            content='SYNTHETIC_SECRET', usage=dict(input_tokens=1, output_tokens=1), stop_reason='end_turn')))
        self.write(); self.assertEqual(self.invoke('import')[1]['imported'], 3)
        self.assertEqual(store.capture(self.source, cursor), old); self.assertEqual(store.graph(self.source, cursor), graph)
        self.assertEqual(old.summary()['fields']['served_model']['declared'], 3)
        self.assertEqual(store.capture(self.source).summary()['fields']['served_model']['declared'], 4)

    def test_partial_tail_preview_cannot_create_or_advance_checkpoint(self):
        full = self.capture.read_bytes(); self.capture.write_bytes(full.rstrip(b'\n'))
        self.assertFalse(self.invoke('preview')[1]['checkpoint_ready'])
        self.assertEqual(self.invoke('import')[0], 2); self.assertFalse(self.cache.parent.exists())
        self.capture.write_bytes(full); self.assertEqual(self.invoke('import')[0], 0); before = self.cache.read_bytes()
        self.capture.write_bytes(full+b'{"type":')
        self.assertEqual(self.invoke('import')[0], 2); self.assertEqual(self.invoke('recheck')[0], 2)
        self.assertEqual(self.cache.read_bytes(), before)

    def test_raw_duplicate_uuid_conflict_is_not_hidden_by_ordinary_body_insensitive_dedup(self):
        baseline = self.fixture.collect()['fingerprint']; self.assertEqual(self.invoke('import')[0], 0); before = self.cache.read_bytes()
        other = deepcopy(self.rows[2]); other['message']['content'][0]['input']['command'] = 'SYNTHETIC_CHANGED_BODY'
        self.rows.append(other); self.write()
        self.assertEqual(self.fixture.collect()['fingerprint'], baseline)
        self.assertEqual(self.invoke('import')[0], 2); self.assertEqual(self.cache.read_bytes(), before)

    def test_graph_excludes_derived_facets_and_keeps_missing_message_parents_unlinked(self):
        self.rows[1]['parentUuid'] = 'outside-capture'; self.write(); snapshot = self.snapshot()
        native = {r.native_id_sha256 for r in snapshot.records if r.provenance == 'native_field'}
        derived = {r.native_id_sha256 for r in snapshot.records if r.provenance == 'unknown'}
        self.assertEqual(len(derived), 4)
        for join in snapshot.graph.joins:
            self.assertIn(join.left.native_id_sha256, native)
            if join.right is not None: self.assertIn(join.right.native_id_sha256, native)
        self.assertEqual(sum(j.status == 'unlinked' for j in snapshot.graph.joins), 1)
        self.assertFalse(any(r.record_type == 'claude_session_start' for r in snapshot.records))
        report = recheck(self.source, self.reader(), snapshot.graph)
        self.assertEqual(report['statuses']['unlinked'], 1); self.assertFalse(report['task_outcomes_verified'])

    def test_self_parent_pointer_keeps_one_evidence_ref_without_inventing_lineage(self):
        self.rows[1]['parentUuid'] = 'u1'; self.write(); snapshot = self.snapshot()
        reflexive = next(j for j in snapshot.graph.joins if j.relation == 'native_message_parent' and j.left == j.right)
        self.assertEqual(len(reflexive.evidence), 1); self.assertEqual(reflexive.status, 'candidate')
        self.assertEqual(self.invoke('import')[0], 0); before = self.cache.read_bytes()
        report = self.invoke('recheck')[1]
        self.assertIsNone(report['independent_roots']); self.assertFalse(report['task_outcomes_verified'])
        self.assertFalse(report['historical_interval_verified']); self.assertEqual(self.cache.read_bytes(), before)

    def test_multi_block_message_has_one_identity_and_revision_bound_membership(self):
        self.rows[2]['message']['content'].append(dict(type='text', text='SYNTHETIC_SECRET')); self.write()
        snapshot = self.snapshot(); self.assertEqual(len(snapshot.metadata.identities), 8)
        self.assertEqual(sum(j.relation == 'native_message_member' for j in snapshot.graph.joins), 1)
        self.assertEqual(self.invoke('import')[0], 0); before = self.cache.read_bytes()
        self.assertEqual(self.invoke('recheck')[1]['statuses']['verified'], 10); self.assertEqual(self.cache.read_bytes(), before)

    def test_stale_raw_revisions_and_forged_relationship_inputs_never_verify(self):
        graph = self.snapshot().graph; self.assertEqual(self.invoke('import')[0], 0); before = self.cache.read_bytes()
        self.rows[2]['message']['content'][0]['input']['command'] = 'SYNTHETIC_CHANGED_BODY'; self.write()
        report = self.invoke('recheck')[1]; self.assertGreater(report['statuses']['unlinked'], 0)
        self.assertEqual(self.cache.read_bytes(), before)
        class Forged(type(self.reader())):
            def read(self, source): raise AssertionError('never execute claimed proof reader')
        for connector in (LocalExport(self.manifest), Forged(self.manifest)):
            with self.assertRaises(NotImplementedError): recheck(self.source, connector, graph)
        with patch('agent_harness_lab.sources.claude.ClaudeCapture.read', side_effect=AssertionError('no native read')):
            for proposed in (replace(graph, project_sha256=digest('foreign')),
                    replace(graph, joins=(replace(graph.joins[0], status='verified', reason='native_proof'),))):
                with self.assertRaises(ValueError): recheck(self.source, self.reader(), proposed)

    def test_invalid_native_version_link_and_selection_reject_without_changing_journal(self):
        self.assertEqual(self.invoke('import')[0], 0); before = self.cache.read_bytes(); full = self.capture.read_bytes()
        self.rows[2]['version'] = 'future'; self.write(); self.assertEqual(self.invoke('import')[0], 2)
        self.capture.write_bytes(full); moved = self.root/'other.jsonl'; self.capture.rename(moved); self.capture.symlink_to(moved)
        self.assertEqual(self.invoke('import')[0], 2); self.assertEqual(self.cache.read_bytes(), before)
        self.capture.unlink(); moved.rename(self.capture)
        self.selected['capture'] = '../outside'; self.declare(); self.assertEqual(self.invoke('preview')[0], 2)
        self.assertEqual(self.cache.read_bytes(), before)

    def test_conversion_selection_and_input_validation_share_one_deadline(self):
        from agent_harness_lab.sources.native import EvidenceRecord, relationships
        clock = [0]
        def delayed(*args, **kwargs):
            result = EvidenceRecord(*args, **kwargs); clock[0] = 11; return result
        with patch('agent_harness_lab.sources.native.time.monotonic', side_effect=lambda: clock[0]), \
             patch('agent_harness_lab.sources.native.EvidenceRecord', delayed), self.assertRaises(TimeoutError): self.snapshot()
        def mutate(*args, **kwargs):
            result = relationships(*args, **kwargs); self.descriptor.write_text('{}'); return result
        with patch('agent_harness_lab.sources.native.relationships', mutate): self.assertEqual(self.invoke('import')[0], 2)
        self.assertFalse(self.cache.exists())
