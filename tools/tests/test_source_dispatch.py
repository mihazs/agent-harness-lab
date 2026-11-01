import contextlib
from dataclasses import replace
import io
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from agent_harness_lab.cli import main
from agent_harness_lab.sources.schema import EvidenceRecord, SourceSpec, digest
from agent_harness_lab.sources.snapshot import LocalExport
from agent_harness_lab.sources.registry import Capability, Registry
from agent_harness_lab.sources.store import SourceStore


class SourceDispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = SourceSpec('local_file', 'ahl-metadata', 'v1', digest(str(self.root.resolve())),
            digest('project'), digest('scope'), identity_provenance='source_declared_id', enabled=True)
        self.record = EvidenceRecord(self.source.source_key, digest('native'), digest('revision'), digest('body'),
            'contextual_assertion', 'page', '2026-01-01T00:00:00Z')
        self.manifest = self.root/'source.json'; self.data = self.root/'records.json'
        self.data.write_text(json.dumps([self.record.to_dict()]))
        self.declare()

    def declare(self, source=None, data='records.json'):
        self.manifest.write_text(json.dumps(dict(schema_version=1, source=(source or self.source).to_dict(), data=data)))

    def invoke(self, operation, *extra):
        output, error = io.StringIO(), io.StringIO()
        args = ['sources', operation, '--manifest', str(self.manifest), '--project-sha256', self.source.project_sha256,
                '--scope-sha256', self.source.scope_sha256, *extra]
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            code = main(args)
        self.assertNotIn('SYNTHETIC_SECRET', output.getvalue()+error.getvalue())
        return code, output.getvalue()

    def test_default_discovery_is_empty_and_does_not_read_connect_or_execute(self):
        with patch('builtins.open', side_effect=AssertionError('no files')), \
                patch('subprocess.Popen', side_effect=AssertionError('no processes')), \
                patch.object(socket, 'socket', side_effect=AssertionError('no network')), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['sources']), 0)
        self.assertEqual(json.loads(output.getvalue())['enabled_sources'], 0)

    def test_preview_import_status_and_reimport_use_the_same_frozen_export(self):
        cache = self.root/'private/history.json'
        with patch.object(socket, 'socket', side_effect=AssertionError('no network')):
            self.assertEqual(self.invoke('probe')[0], 0)
            code, result = self.invoke('preview')
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(result)['records'], 1)
            self.assertFalse(cache.exists())
            code, result = self.invoke('import', '--cache', str(cache))
            self.assertEqual((code, json.loads(result)['imported']), (0, 1))
            self.assertEqual(json.loads(self.invoke('import', '--cache', str(cache))[1])['skipped'], 1)
            status = json.loads(self.invoke('status', '--cache', str(cache))[1])
        self.assertEqual(status['revisions'], 1)
        self.assertEqual(status['capture_completeness'], 'unknown')

    def test_disabled_unsupported_or_foreign_scope_never_opens_record_data(self):
        with patch('agent_harness_lab.sources.snapshot.read_regular', side_effect=AssertionError('no data read')):
            for source, expected in ((replace(self.source, enabled=False), 3),
                    (replace(self.source, version='unknown'), 3),
                    (replace(self.source, project_sha256=digest('foreign')), 2)):
                self.declare(source)
                self.assertEqual(self.invoke('preview')[0], expected)

    def test_root_mismatch_traversal_links_and_partial_files_are_rejected(self):
        self.declare(replace(self.source, root_sha256=digest('other-root')))
        self.assertEqual(self.invoke('preview')[0], 2)
        self.declare(data='../secret')
        self.assertEqual(self.invoke('preview')[0], 2)
        self.declare(); self.data.unlink(); self.data.symlink_to(self.manifest)
        self.assertEqual(self.invoke('preview')[0], 2)
        self.data.unlink(); self.data.write_text('[{"body":"SYNTHETIC_SECRET"')
        self.assertEqual(self.invoke('import', '--cache', str(self.root/'cache.json'))[0], 2)
        self.assertFalse((self.root/'cache.json').exists())

    def test_conflicting_ids_foreign_records_and_plaintext_fields_cannot_advance_cache(self):
        cache = self.root/'cache.json'
        self.assertEqual(self.invoke('import', '--cache', str(cache))[0], 0)
        before = cache.read_bytes()
        for rows in ([self.record.to_dict(), replace(self.record, content_sha256=digest('changed')).to_dict()],
                     [replace(self.record, source_sha256=digest('foreign')).to_dict()],
                     [{**self.record.to_dict(), 'body': 'SYNTHETIC_SECRET'}]):
            self.data.write_text(json.dumps(rows))
            self.assertEqual(self.invoke('import', '--cache', str(cache))[0], 2)
            self.assertEqual(cache.read_bytes(), before)

    def test_declaration_change_invalidates_previously_resolved_source(self):
        connector = LocalExport(self.manifest)
        self.declare(replace(self.source, enabled=False))
        with self.assertRaises(ValueError): connector.read(self.source)

    def test_mutable_export_can_return_to_an_earlier_snapshot_without_losing_history(self):
        cache = self.root/'cache.json'
        original = self.data.read_bytes()
        self.assertEqual(self.invoke('import', '--cache', str(cache))[0], 0)
        changed = replace(self.record, revision_sha256=digest('revision-2'), content_sha256=digest('changed'))
        self.data.write_text(json.dumps([changed.to_dict()]))
        self.assertEqual(self.invoke('import', '--cache', str(cache))[0], 0)
        self.data.write_bytes(original)
        code, receipt = self.invoke('import', '--cache', str(cache))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(receipt)['imported'], 0)
        self.assertEqual(json.loads(self.invoke('status', '--cache', str(cache))[1])['revisions'], 2)

    def test_registry_rejects_wrong_planes_and_store_scope_before_commit(self):
        collector = LocalExport(self.manifest)
        registry = Registry()
        registry.register(Capability('local_file', 'ahl-metadata', 'v1', ('behavioral_event',)), collector)
        scope = dict(project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256)
        store = SourceStore(self.root/'cache.json', **scope)
        with self.assertRaises(ValueError): registry.ingest(self.source, store, **scope)
        self.assertFalse(store.path.exists())
        foreign = SourceStore(self.root/'foreign.json', project_sha256=digest('foreign'), scope_sha256=self.source.scope_sha256)
        with patch.object(collector, 'read', side_effect=AssertionError('scope must precede collection')):
            with self.assertRaises(ValueError): registry.ingest(self.source, foreign, **scope)
