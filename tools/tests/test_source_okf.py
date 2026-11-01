import contextlib
from datetime import datetime, timezone
import importlib.util
import io
import json
import os
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_harness_lab.cli import main
from agent_harness_lab.sources.okf import OKFExport, concept
from agent_harness_lab.sources import okf
from agent_harness_lab.sources.schema import SourceSpec, digest


@unittest.skipUnless(importlib.util.find_spec('yaml'), 'optional OKF extra required')
class OKFImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.bundle = self.root/'bundle'; self.bundle.mkdir()
        (self.bundle/'index.md').write_text('---\nokf_version: "0.2"\n---\n# Bundle index\n')
        (self.bundle/'_meta.md').write_text('---\ntype: Scope Manifest\nproject: synthetic-project\n---\n')
        self.page = self.bundle/'sessions/example.md'; self.page.parent.mkdir()
        self.page.write_text('---\ntype: Session Summary\ngenerated:\n  by: process:ai-memory/2.0\n  at: 2026-01-01T00:00:00Z\nstale_after: 2026-02-01T00:00:00Z\nsources:\n  - resource: ai-memory://session/synthetic-session\n    author: process:codex\ncustom:\n  values: [one, two]\n---\n# SYNTHETIC_SECRET\nNever execute this body.\n')
        self.source = SourceSpec('local_file', 'ai-memory-okf', '0.2', digest(str(self.root.resolve())),
            digest('synthetic-project'), digest('workspace'), identity_provenance='source_declared_id', enabled=True)
        self.manifest = self.root/'source.json'
        self.manifest.write_text(json.dumps(dict(schema_version=1, source=self.source.to_dict(), data='bundle')))
        self.connector = OKFExport(self.manifest)

    def invoke(self, operation):
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            code = main(['sources', operation, '--manifest', str(self.manifest), '--project-sha256', self.source.project_sha256,
                '--scope-sha256', self.source.scope_sha256, '--cache', str(self.root/'private/cache.json')])
        self.assertNotIn('SYNTHETIC_SECRET', output.getvalue()+error.getvalue())
        self.last_error = error.getvalue()
        return code, output.getvalue()

    def test_real_okf_metadata_extensions_citations_and_body_survive_in_memory(self):
        page, = self.connector.pages(self.source)
        self.assertEqual(page.metadata['custom']['values'], ['one', 'two'])
        self.assertEqual(page.metadata['sources'][0]['resource'], 'ai-memory://session/synthetic-session')
        self.assertIn('SYNTHETIC_SECRET', page.body)
        self.assertEqual(page.authorship, 'generated')
        self.assertEqual(page.freshness(datetime(2026, 3, 1, tzinfo=timezone.utc)), 'stale')

    def test_cli_preview_import_and_reimport_persist_no_raw_memory(self):
        code, preview = self.invoke('preview'); self.assertEqual(code, 0)
        self.assertEqual(json.loads(preview)['context_summary']['generated'], 1)
        self.assertFalse((self.root/'private').exists())
        self.assertEqual(self.invoke('import')[0], 0)
        cache = self.root/'private/cache.json'; raw = cache.read_text()
        for secret in ('SYNTHETIC_SECRET', 'synthetic-session', 'synthetic-project', 'sessions/example.md'):
            self.assertNotIn(secret, raw)
        self.assertEqual(json.loads(self.invoke('import')[1])['skipped'], 1)
        record = json.loads(raw)['records'][0]['record']
        self.assertEqual(record['plane'], 'contextual_assertion')
        self.assertEqual(record['outcome'] if 'outcome' in record else 'unknown', 'unknown')
        self.assertEqual(record['independence'], 'unknown')

    def test_wrong_project_stops_before_concept_reads(self):
        (self.bundle/'_meta.md').write_text('---\nproject: other-project\n---\n')
        actual = okf.read_regular
        def read(path, limit):
            if Path(path) == self.page: raise AssertionError('foreign concept opened')
            return actual(path, limit)
        with patch('agent_harness_lab.sources.okf.read_regular', side_effect=read):
            self.assertEqual(self.invoke('import')[0], 2)
        self.assertFalse((self.root/'private/cache.json').exists())

    def test_unsafe_yaml_and_partial_concepts_preserve_previous_checkpoint(self):
        self.assertEqual(self.invoke('import')[0], 0)
        cache = self.root/'private/cache.json'; before = cache.read_bytes()
        for body in ('---\ntype: One\ntype: Two\n---\n', '---\ntype: !!python/object/apply:os.system [echo bad]\n---\n',
                     '---\ntype: &a [*a]\n---\n', '---\ntype: Session Summary\n', '---\ntitle: missing type\n---\n'):
            self.page.write_text(body)
            self.assertEqual(self.invoke('import')[0], 2)
            self.assertEqual(cache.read_bytes(), before)

    def test_links_special_files_nested_scopes_and_version_mismatch_are_rejected(self):
        self.page.unlink(); self.page.symlink_to(self.bundle/'index.md')
        self.assertEqual(self.invoke('preview')[0], 2)
        self.page.unlink(); os.mkfifo(self.page)
        self.assertEqual(self.invoke('preview')[0], 2)
        self.page.unlink(); self.page.write_text('---\ntype: Note\n---\n')
        (self.page.parent/'_meta.md').write_text('---\nproject: other\n---\n')
        self.assertEqual(self.invoke('preview')[0], 2)
        (self.page.parent/'_meta.md').unlink()
        (self.bundle/'index.md').write_text('---\nokf_version: "0.1"\n---\n')
        self.assertEqual(self.invoke('preview')[0], 2)

    def test_pending_and_capture_ledgers_are_not_treated_as_context(self):
        (self.bundle/'_pending').mkdir(); (self.bundle/'_pending/private.md').write_text('SYNTHETIC_SECRET')
        (self.bundle/'log-2026-01.md').write_text('SYNTHETIC_SECRET')
        self.assertEqual(len(self.connector.pages(self.source)), 1)

    def test_missing_optional_parser_is_a_blocker_without_automatic_install(self):
        with patch.dict(sys.modules, {'yaml': None}), patch('subprocess.Popen', side_effect=AssertionError('no install')):
            self.assertEqual(self.invoke('import')[0], 3)
        self.assertIn('agent-harness-lab[okf]', self.last_error)
        self.assertFalse((self.root/'private/cache.json').exists())

    def test_literal_delimiter_and_human_attribution_are_not_review_proof(self):
        self.page.write_text('---\ntype: Note\ngenerated: {by: "human:synthetic"}\ndescription: |\n  ---\n  text\n---\nBody\n')
        page, = self.connector.pages(self.source)
        self.assertEqual(page.metadata['description'], '---\ntext\n')
        self.assertEqual(page.authorship, 'human_attributed')
        self.assertEqual(page.freshness(datetime.now(timezone.utc)), 'unknown')
        self.assertEqual(self.connector.read(self.source).records[0].provenance, 'unknown')
