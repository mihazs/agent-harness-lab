import contextlib
from datetime import datetime, timezone
import importlib.util
import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.sources.okf import OKFExport
from agent_harness_lab.sources import okf
from agent_harness_lab.sources.schema import SourceSpec, digest


@pytest.mark.skipif(not importlib.util.find_spec('yaml'), reason='optional OKF extra required')
class TestOKFImport:
    @pytest.fixture(autouse=True)
    def _bundle(self, tmp_path):
        self.root = tmp_path; self.bundle = self.root/'bundle'; self.bundle.mkdir()
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
        assert 'SYNTHETIC_SECRET' not in output.getvalue()+error.getvalue()
        self.last_error = error.getvalue()
        return code, output.getvalue()

    def test_real_okf_metadata_extensions_citations_and_body_survive_in_memory(self):
        page, = self.connector.pages(self.source)
        assert page.metadata['custom']['values'] == ['one', 'two']
        assert page.metadata['sources'][0]['resource'] == 'ai-memory://session/synthetic-session'
        assert 'SYNTHETIC_SECRET' in page.body
        assert page.authorship == 'generated'
        assert page.freshness(datetime(2026, 3, 1, tzinfo=timezone.utc)) == 'stale'

    def test_cli_preview_import_and_reimport_persist_no_raw_memory(self):
        code, preview = self.invoke('preview'); assert code == 0
        assert json.loads(preview)['context_summary']['generated'] == 1
        assert not (self.root/'private').exists()
        assert self.invoke('import')[0] == 0
        cache = self.root/'private/cache.json'; raw = cache.read_text()
        for secret in ('SYNTHETIC_SECRET', 'synthetic-session', 'synthetic-project', 'sessions/example.md'):
            assert secret not in raw
        assert json.loads(self.invoke('import')[1])['skipped'] == 1
        record = json.loads(raw)['records'][0]['record']
        assert record['plane'] == 'contextual_assertion'
        assert (record['outcome'] if 'outcome' in record else 'unknown') == 'unknown'
        assert record['independence'] == 'unknown'

    def test_wrong_project_stops_before_concept_reads(self):
        (self.bundle/'_meta.md').write_text('---\nproject: other-project\n---\n')
        actual = okf.read_regular
        def read(path, limit):
            if Path(path) == self.page: raise AssertionError('foreign concept opened')
            return actual(path, limit)
        with patch('agent_harness_lab.sources.okf.read_regular', side_effect=read):
            assert self.invoke('import')[0] == 2
        assert not (self.root/'private/cache.json').exists()

    def test_unsafe_yaml_and_partial_concepts_preserve_previous_checkpoint(self):
        assert self.invoke('import')[0] == 0
        cache = self.root/'private/cache.json'; before = cache.read_bytes()
        for body in ('---\ntype: One\ntype: Two\n---\n', '---\ntype: !!python/object/apply:os.system [echo bad]\n---\n',
                     '---\ntype: &a [*a]\n---\n', '---\ntype: Session Summary\n', '---\ntitle: missing type\n---\n'):
            self.page.write_text(body)
            assert self.invoke('import')[0] == 2
            assert cache.read_bytes() == before

    def test_links_special_files_nested_scopes_and_version_mismatch_are_rejected(self):
        self.page.unlink(); self.page.symlink_to(self.bundle/'index.md')
        assert self.invoke('preview')[0] == 2
        self.page.unlink(); os.mkfifo(self.page)
        assert self.invoke('preview')[0] == 2
        self.page.unlink(); self.page.write_text('---\ntype: Note\n---\n')
        (self.page.parent/'_meta.md').write_text('---\nproject: other\n---\n')
        assert self.invoke('preview')[0] == 2
        (self.page.parent/'_meta.md').unlink()
        (self.bundle/'index.md').write_text('---\nokf_version: "0.1"\n---\n')
        assert self.invoke('preview')[0] == 2

    def test_pending_and_capture_ledgers_are_not_treated_as_context(self):
        (self.bundle/'_pending').mkdir(); (self.bundle/'_pending/private.md').write_text('SYNTHETIC_SECRET')
        (self.bundle/'log-2026-01.md').write_text('SYNTHETIC_SECRET')
        assert len(self.connector.pages(self.source)) == 1

    def test_missing_optional_parser_is_a_blocker_without_automatic_install(self):
        with patch.dict(sys.modules, {'yaml': None}), patch('subprocess.Popen', side_effect=AssertionError('no install')):
            assert self.invoke('import')[0] == 3
        assert 'agent-harness-lab[okf]' in self.last_error
        assert not (self.root/'private/cache.json').exists()

    def test_literal_delimiter_and_human_attribution_are_not_review_proof(self):
        self.page.write_text('---\ntype: Note\ngenerated: {by: "human:synthetic"}\ndescription: |\n  ---\n  text\n---\nBody\n')
        page, = self.connector.pages(self.source)
        assert page.metadata['description'] == '---\ntext\n'
        assert page.authorship == 'human_attributed'
        assert page.freshness(datetime.now(timezone.utc)) == 'unknown'
        assert self.connector.read(self.source).records[0].provenance == 'unknown'

    def test_full_large_pages_frontmatter_and_aggregate_are_retained(self):
        content = '---\ntype: Note\ndescription: '+ 'm'*20000 + '\n---\n' + 'SYNTHETIC_BODY'*25000
        for i in range(14): (self.bundle/f'large-{i}.md').write_text(content)
        for name in ('index.md','_meta.md'):
            path = self.bundle/name
            path.write_text(path.read_text()+'x'*70000)
        pages = self.connector.pages(self.source)
        assert len(pages) == 15
        large = [page for page in pages if page.path.startswith('large-')]
        assert len(large) == 14
        assert all(page.metadata['description'] == 'm'*20000 for page in large)
        assert all(page.body == 'SYNTHETIC_BODY'*25000 for page in large)
        assert sum(len(path.read_bytes()) for path in self.bundle.glob('large-*.md')) > 4*1024*1024

    def test_all_pages_beyond_old_directory_and_record_counts_are_read(self):
        for i in range(2001): (self.bundle/f'page-{i}.md').write_text('---\ntype: Note\n---\nSynthetic\n')
        snapshot = self.connector.read(self.source)
        assert len(snapshot.records) == 2002
        assert all(row.independence == 'unknown' for row in snapshot.records)
