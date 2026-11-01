"""Synthetic native journals: exact revisions survive append, edit and failed reads."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from dataclasses import replace
import io
import json
import os
import unittest
from unittest.mock import patch
if __package__:
    from . import test_codex_rollout as fixtures
else:
    import test_codex_rollout as fixtures
from agent_harness_lab.cli import main
from agent_harness_lab.evidence import collect
from agent_harness_lab.sources.schema import SourceSpec, digest
from agent_harness_lab.sources.store import SourceStore


class NativeSourceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CodexRolloutTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.project, self.capture = self.fixture.root, self.fixture.project, self.fixture.path
        self.rows = deepcopy(self.fixture.rows)
        self.manifest, self.descriptor = self.root/'source.json', self.root/'selection.json'
        self.cache = self.root/'private/journal.json'
        self.selected = dict(schema_version=1, project='project', capture='native.jsonl', max_records=10000)
        self.declare()

    def declare(self):
        self.source = SourceSpec('local_file', 'codex-rollout', '0.166.0-v1', digest(str(self.root.resolve())),
            digest('synthetic-project'), digest(self.selected), identity_provenance='source_declared_id', enabled=True)
        self.descriptor.write_text(json.dumps(self.selected))
        self.manifest.write_text(json.dumps(dict(schema_version=1, source=self.source.to_dict(), data='selection.json')))
        self.scope = dict(project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256)

    def write(self): self.capture.write_text(''.join(json.dumps(r)+'\n' for r in self.rows))

    def store(self): return SourceStore(self.cache, **self.scope)

    def reader(self):
        from agent_harness_lab.sources.codex import CodexCapture
        return CodexCapture(self.manifest)

    def invoke(self, operation, *extra):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(['sources', operation, '--manifest', str(self.manifest), '--project-sha256', self.source.project_sha256,
                '--scope-sha256', self.source.scope_sha256, '--cache', str(self.cache), *extra])
        for private in ('SYNTHETIC_SECRET', str(self.root), 'synthetic-thread'):
            self.assertNotIn(private, out.getvalue()+err.getvalue())
        return code, json.loads(out.getvalue()) if out.getvalue() else None

    def test_native_cli_preview_and_import_use_shared_contracts_and_preserve_privacy(self):
        code, result = self.invoke('preview')
        self.assertEqual(code, 0); self.assertTrue(result['checkpoint_ready'])
        self.assertEqual(result['records'], 6); self.assertEqual(result['evidence_graph']['nodes'], 6)
        self.assertFalse(result['network_used']); self.assertFalse(self.cache.exists())
        self.assertEqual(self.invoke('import')[0], 0)
        status = self.invoke('status')[1]
        self.assertEqual(status['revisions'], 6); self.assertEqual(status['execution_metadata']['identities'], 2)
        self.assertEqual(status['evidence_graph']['joins'], 6)
        self.assertEqual(self.cache.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('SYNTHETIC_SECRET', self.cache.read_text()); self.assertNotIn(str(self.root), self.cache.read_text())

    def test_probe_scope_disabled_and_unsupported_versions_do_not_read_native_capture(self):
        with patch('agent_harness_lab.sources.codex.CodexCapture.read', side_effect=AssertionError('no capture read')):
            self.assertEqual(self.invoke('probe')[0], 0)
            self.assertEqual(self.invoke('preview', '--project-sha256', digest('foreign'))[0], 2)
            for changed in (replace(self.source, enabled=False), replace(self.source, version='future')):
                self.manifest.write_text(json.dumps(dict(schema_version=1, source=changed.to_dict(), data='selection.json')))
                self.assertEqual(self.invoke('preview')[0], 3)

    def test_declared_native_identity_never_becomes_effective_settings_or_task_success(self):
        snapshot = self.reader().read(self.source)
        self.assertEqual(len(snapshot.metadata.identities), 2)
        summary = snapshot.metadata.summary()
        self.assertEqual(summary['fields']['requested_model']['declared'], 1)
        self.assertEqual(summary['fields']['requested_model']['unknown'], 1)
        for field in ('served_model', 'effective_harness', 'effective_config', 'memory_snapshot', 'grader_revision', 'task_root'):
            self.assertEqual(summary['fields'][field]['unknown'], 2)
        self.assertTrue(all(f.status == 'declared' for i in snapshot.metadata.identities for f in i.fields))
        self.assertTrue(all(r.plane == 'behavioral_event' and r.independence == 'unknown' for r in snapshot.records))
        self.assertIsNone(snapshot.graph.summary()['independent_roots'])
        (self.project/'AGENTS.md').write_text('SYNTHETIC_CHANGED_CONFIG')
        after = self.reader().read(self.source)
        self.assertEqual(after.sha256, snapshot.sha256); self.assertEqual(after.metadata, snapshot.metadata)
        self.assertEqual(after.graph, snapshot.graph)

    def test_reimport_is_native_revision_and_checkpoint_idempotent(self):
        self.assertEqual(self.invoke('import')[1]['imported'], 6)
        store = self.store(); cursor = store.status(self.source)['cursor_sha256']; original = self.cache.read_bytes()
        second = self.invoke('import')[1]
        self.assertEqual(second['imported'], 0); self.assertEqual(second['skipped'], 6)
        self.assertEqual(store.status(self.source)['cursor_sha256'], cursor); self.assertEqual(self.cache.read_bytes(), original)

    def test_late_appended_context_preserves_old_model_request_and_membership(self):
        self.assertEqual(self.invoke('import')[0], 0); store = self.store()
        cursor = store.status(self.source)['cursor_sha256']; old = store.capture(self.source, cursor); graph = store.graph(self.source, cursor)
        self.rows.append(self.fixture.row('turn_context', dict(cwd=str(self.project), model='SYNTHETIC_NEW_MODEL', turn_id='new-turn'), 6))
        self.write(); self.assertEqual(self.invoke('import')[0], 0)
        self.assertEqual(store.capture(self.source, cursor), old); self.assertEqual(store.graph(self.source, cursor), graph)
        current = store.capture(self.source)
        self.assertEqual(current.summary()['fields']['requested_model']['declared'], 2)
        self.assertEqual(old.summary()['fields']['requested_model']['declared'], 1)

    def test_body_only_edit_versions_native_record_without_retriggering_metadata_monitor(self):
        first = collect(self.project, events=self.capture, source_format='codex', source_version=fixtures.VERSION)
        self.assertEqual(self.invoke('import')[0], 0); store = self.store()
        before = self.reader().read(self.source)
        call = next(r for r in before.records if r.record_type == 'codex_tool_call')
        self.rows[2]['payload']['arguments'] = 'SYNTHETIC_CHANGED_BODY'; self.write()
        result = self.invoke('import')[1]
        self.assertEqual(result['imported'], 1); self.assertEqual(len(store.history(self.source, call.native_id_sha256)), 2)
        self.assertEqual(collect(self.project, events=self.capture, source_format='codex', source_version=fixtures.VERSION)['fingerprint'], first['fingerprint'])
        self.assertNotIn('SYNTHETIC_CHANGED_BODY', self.cache.read_text())

    def test_partial_preview_is_visible_but_import_cannot_create_or_advance_a_checkpoint(self):
        full = self.capture.read_bytes(); self.capture.write_bytes(full.rstrip(b'\n'))
        code, preview = self.invoke('preview')
        self.assertEqual(code, 0); self.assertFalse(preview['checkpoint_ready'])
        self.assertTrue(preview['native_summary']['partial_tail'])
        self.assertEqual(self.invoke('import')[0], 2); self.assertFalse(self.cache.parent.exists())
        self.capture.write_bytes(full); self.assertEqual(self.invoke('import')[0], 0); original = self.cache.read_bytes()
        self.capture.write_bytes(full.rstrip(b'\n')); self.assertEqual(self.invoke('import')[0], 2)
        self.assertEqual(self.cache.read_bytes(), original)

    def test_failed_decode_unknown_version_and_linked_inputs_preserve_committed_state(self):
        self.assertEqual(self.invoke('import')[0], 0); original = self.cache.read_bytes(); full = self.capture.read_bytes()
        self.capture.write_bytes(full+b'{bad}\n'); self.assertEqual(self.invoke('import')[0], 2)
        self.assertEqual(self.cache.read_bytes(), original)
        self.rows[0]['payload']['cli_version'] = 'future'; self.write(); self.assertEqual(self.invoke('import')[0], 2)
        self.capture.write_bytes(full); linked = self.root/'other.jsonl'; self.capture.rename(linked); self.capture.symlink_to(linked)
        self.assertEqual(self.invoke('import')[0], 2); self.assertEqual(self.cache.read_bytes(), original)

    def test_descriptor_and_manifest_redirection_changes_during_capture_reject_before_commit(self):
        self.assertEqual(self.invoke('import')[0], 0); original = self.cache.read_bytes()
        from agent_harness_lab.sources.codex import decode
        declaration = self.manifest.read_text()
        def mutate_descriptor(*args, **kwargs):
            result = decode(*args, **kwargs)
            changed = {**self.selected, 'max_records': 9999}; self.descriptor.write_text(json.dumps(changed))
            return result
        with patch('agent_harness_lab.sources.codex.decode', mutate_descriptor): self.assertEqual(self.invoke('import')[0], 2)
        self.descriptor.write_text(json.dumps(self.selected))
        def redirect_manifest(*args, **kwargs):
            result = decode(*args, **kwargs); (self.root/'other-selection.json').write_text(self.descriptor.read_text())
            self.manifest.write_text(json.dumps(dict(schema_version=1, source=self.source.to_dict(), data='other-selection.json')))
            return result
        with patch('agent_harness_lab.sources.codex.decode', redirect_manifest): self.assertEqual(self.invoke('import')[0], 2)
        self.manifest.write_text(declaration); self.assertEqual(self.cache.read_bytes(), original)

    def test_forged_proof_labels_and_parent_fork_metadata_do_not_upgrade_execution_identity(self):
        self.rows[0]['payload'].update(parent_thread_id='native-parent', forked_from_id='other-fork', subagent_history_start_ordinal=1,
            execution_identity=dict(status='verified', effective_harness='SYNTHETIC_SECRET'))
        self.write(); reader = self.reader(); snapshot = reader.read(self.source)
        header = snapshot.metadata.identities[0]
        parent = next(f for f in header.fields if f.name == 'parent_session')
        self.assertEqual(parent.status, 'declared'); self.assertEqual(parent.namespace, 'codex.thread')
        self.assertFalse(any(f.status == 'verified' or f.name == 'effective_harness' for i in snapshot.metadata.identities for f in i.fields))
        self.assertEqual(self.invoke('import')[0], 0); self.assertNotIn('SYNTHETIC_SECRET', self.cache.read_text())

    def test_derived_pending_identity_survives_other_pending_calls_resolving(self):
        self.rows = self.rows[:3]; self.write(); self.assertEqual(self.invoke('import')[0], 0)
        self.rows.append(self.fixture.row('response_item', dict(type='function_call', name='exec_command', call_id='second', arguments='SYNTHETIC_SECRET'), 3))
        self.write(); self.assertEqual(self.invoke('import')[0], 0)
        before = self.reader().read(self.source)
        pending = [r for r in before.records if r.record_type == 'codex_capture_coverage']
        self.assertEqual(len(pending), 2)
        self.rows.append(self.fixture.row('response_item', dict(type='function_call_output', call_id='SYNTHETIC_SECRET', output='SYNTHETIC_SECRET'), 4))
        self.write(); self.assertEqual(self.invoke('import')[0], 0)
        after = self.reader().read(self.source)
        remaining = next(r for r in after.records if r.record_type == 'codex_capture_coverage')
        self.assertIn(remaining.native_id_sha256, {r.native_id_sha256 for r in pending})
        self.assertEqual(len(self.store().history(self.source, remaining.native_id_sha256)), 1)

    def test_limits_and_traversal_are_rejected_without_source_or_cache_mutation(self):
        original = dict(self.selected)
        with patch('agent_harness_lab.sources.codex.decode', side_effect=AssertionError('no capture reads')):
            for change in (dict(capture='../outside.jsonl'), dict(max_records=True), dict(max_records=10001)):
                self.selected = {**original, **change}; self.declare(); self.assertEqual(self.invoke('preview')[0], 2)
        self.assertFalse(self.cache.exists())

    def test_native_conversion_and_validation_remain_under_the_shared_deadline(self):
        from agent_harness_lab.sources.codex import EvidenceRecord
        clock = [0]
        def delayed(*args, **kwargs):
            result = EvidenceRecord(*args, **kwargs); clock[0] = 11
            return result
        with patch('agent_harness_lab.sources.codex.time.monotonic', side_effect=lambda: clock[0]), \
             patch('agent_harness_lab.sources.codex.EvidenceRecord', delayed), self.assertRaises(TimeoutError):
            self.reader().read(self.source)
        self.assertFalse(self.cache.exists())
