"""Synthetic session correlation: one session is counted once; ambiguous claims stay separate."""
import importlib.util
import json

import pytest

from tests.integration.sources.test_source_claude import ClaudeSourceTests
from tests.integration.sources.test_source_codex import NativeSourceTests as CodexSourceTests
from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.sources.graph import JoinReceipt
from agent_harness_lab.sources.okf import OKFExport
from agent_harness_lab.sources.schema import SourceSpec, digest
from agent_harness_lab.sources.session_correlation import PROFILE, correlate_sessions
from agent_harness_lab.sources.store import SourceStore

SECRET = 'SYNTHETIC_SESSION_BODY'


def page(kind, **fields):
    lines = ['---', 'type: '+json.dumps(kind)] + [name+': '+json.dumps(value) for name, value in fields.items()]
    return '\n'.join(lines+['---', '# '+SECRET, ''])


@pytest.mark.skipif(not importlib.util.find_spec('yaml'), reason='optional OKF extra required')
class TestSessionCorrelation:
    @pytest.fixture(autouse=True)
    def _journals(self, tmp_path):
        self.claude, self.codex = ClaudeSourceTests(), CodexSourceTests()
        for name, helper in (('claude', self.claude), ('codex', self.codex)):
            (tmp_path/name).mkdir(); helper.setUp(tmp_path/name)
            assert helper.invoke('import')[0] == 0
        self.root = tmp_path/'memory'; self.bundle = self.root/'bundle'; self.bundle.mkdir(parents=True)
        (self.bundle/'index.md').write_text('---\nokf_version: "0.2"\n---\n# Bundle index\n')
        (self.bundle/'_meta.md').write_text('---\ntype: Scope Manifest\nproject: synthetic-project\n---\n')
        self.session('claude', 'synthetic-session'); self.session('codex', 'synthetic-thread'); self.session('orphan', 'orphan')
        self.source = SourceSpec('local_file', 'ai-memory-okf', '0.2', digest(str(self.root.resolve())),
            digest('synthetic-project'), digest('synthetic-workspace'), identity_provenance='source_declared_id', enabled=True)
        self.manifest = self.root/'source.json'
        self.manifest.write_text(json.dumps(dict(schema_version=1, source=self.source.to_dict(), data='bundle')))
        self.cache = self.root/'private/cache.json'

    def session(self, name, sid, **extra):
        path = self.bundle/'sessions'/(name+'.md'); path.parent.mkdir(exist_ok=True)
        path.write_text(page('Session Summary', session_id=sid, project='synthetic-project', **extra))

    def record(self, path, kind, **fields):
        target = self.bundle/path; target.parent.mkdir(exist_ok=True)
        target.write_text(page(kind, project='synthetic-project', created_at='2026-01-02T03:04:05Z', **fields))

    def correlate(self, natives=None, imported=True):
        if imported:
            assert main(['sources', 'import', '--manifest', str(self.manifest), '--project-sha256', self.source.project_sha256,
                '--scope-sha256', self.source.scope_sha256, '--cache', str(self.cache)]) == 0
        store = SourceStore(self.cache, project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256)
        natives = natives if natives is not None else [(h.source, h.store(), str(h.project.resolve())) for h in (self.claude, self.codex)]
        before = self.cache.read_bytes()
        report = correlate_sessions(OKFExport(self.manifest), self.source, store, natives)
        assert self.cache.read_bytes() == before
        text = json.dumps(report)
        for private in (SECRET, 'synthetic-project', 'synthetic-session', 'synthetic-thread', 'orphan', 'sessions/', str(self.root)):
            assert private not in text
        assert report['profile'] == PROFILE and not public_export_allowed([report])
        assert report['independent_roots'] is None
        assert not (report['delivery_observed'] or report['injection_observed'] or report['verification_authorized'])
        for receipt in report['receipts']:
            assert (JoinReceipt.parse(receipt).status, JoinReceipt.parse(receipt).basis) == ('candidate', 'source_declaration')
        return report

    def by_status(self, report):
        return sorted((c['status'], len(c['memory_sessions']), len(c['native_sessions']), len(c['handoffs']), tuple(c['reasons']))
                      for c in report['clusters'])

    def test_session_in_native_wiki_and_handoff_is_one_cluster(self):
        self.record('handoffs/h.md', 'Handoff', handoff_id='h-one', from_session='synthetic-session', linked_sessions=['synthetic-session'])
        report = self.correlate()
        assert self.by_status(report) == [('linked', 1, 1, 0, ()), ('linked', 1, 1, 1, ()), ('memory_only', 1, 0, 0, ('no_native_match',))]
        assert report['counts']['clusters'] == 3 and report['counts']['handoffs_attached'] == 1
        assert len(report['receipts']) == 2

    def test_same_session_captured_in_two_journals_is_still_one_cluster(self):
        report = self.correlate(natives=[(self.claude.source, self.claude.store(), str(self.claude.project.resolve())),
                                         (self.claude.source, self.claude.store(), str(self.claude.project.resolve()))])
        linked = [c for c in report['clusters'] if c['status'] == 'linked']
        assert len(linked) == 1 and len(linked[0]['native_sessions']) == 2
        assert report['counts']['clusters'] == 3  # claude, codex page (no native match here) and orphan

    def test_duplicate_page_ids_and_cross_runtime_matches_stay_separate(self):
        self.session('claude-copy', 'synthetic-session')
        report = self.correlate()
        reasons = [(c['status'], c['reasons']) for c in report['clusters'] if c['reasons']]
        assert ('memory_only', ['duplicate_session_id']) in reasons
        assert sum(c['status'] == 'memory_only' and c['reasons'] == ['duplicate_session_id'] for c in report['clusters']) == 2
        assert not any(c['status'] == 'linked' and c['memory_sessions'] and any(
            'synthetic-session' in json.dumps(m) for m in c['memory_sessions']) for c in report['clusters'])
        # The native Claude session no clean page binds becomes its own native-only cluster.
        assert any(c['status'] == 'native_only' for c in report['clusters'])

    def test_handoff_naming_two_sessions_does_not_merge_them(self):
        self.record('workstreams/w.md', 'Workstream', workstream_id='w-one', linked_sessions=['synthetic-session', 'synthetic-thread'])
        report = self.correlate()
        assert report['counts']['handoffs_spanning_sessions'] == 1 and report['counts']['handoffs_attached'] == 0
        assert len([c for c in report['clusters'] if c['status'] == 'linked']) == 2

    def test_foreign_project_native_journal_and_stale_bundle_bind_nothing(self):
        report = self.correlate(natives=[(self.claude.source, self.claude.store(), '/foreign/project')])
        assert not any(c['native_sessions'] and c['memory_sessions'] for c in report['clusters'])
        self.session('late', 'late')
        stale = self.correlate(imported=False)
        assert (stale['status'], stale['reason'], stale['clusters']) == ('unknown', 'bundle_not_latest_checkpoint', [])

    def test_inputs_are_validated_and_nothing_is_written_without_a_native_journal(self):
        with pytest.raises(ValueError): correlate_sessions(object(), self.source, None, [])
        with pytest.raises(ValueError): self.correlate(natives=[(self.claude.source, self.claude.store(), 'relative/project')])
        with pytest.raises(ValueError): self.correlate(natives=[(self.source, self.claude.store(), '/abs')])
        assert self.correlate(natives=[])['counts']['statuses'] == {'memory_only': 3}
