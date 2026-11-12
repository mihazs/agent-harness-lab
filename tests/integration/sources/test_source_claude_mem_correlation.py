"""Synthetic Claude-Mem to native session correlation: one session is counted once; ambiguous claims stay separate."""
import copy
import json

import pytest

from tests.integration.sources.test_source_claude import ClaudeSourceTests
from tests.integration.sources.test_source_codex import NativeSourceTests as CodexSourceTests
from tests.unit.sources.test_source_claude_mem import EXPORT
from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.sources.claude_mem import ClaudeMemExport
from agent_harness_lab.sources.claude_mem_correlation import PROFILE, correlate_native_sessions
from agent_harness_lab.sources.graph import JoinReceipt
from agent_harness_lab.sources.schema import SourceSpec, digest
from agent_harness_lab.sources.store import SourceStore


def session(identity, content, platform):
    return dict(EXPORT['sessions'][0], id=identity, content_session_id=content, memory_session_id='memory-'+content, platform_source=platform)


class TestClaudeMemCorrelation:
    @pytest.fixture(autouse=True)
    def _journals(self, tmp_path):
        self.claude, self.codex = ClaudeSourceTests(), CodexSourceTests()
        for name, helper in (('claude', self.claude), ('codex', self.codex)):
            (tmp_path/name).mkdir(); helper.setUp(tmp_path/name)
            assert helper.invoke('import')[0] == 0
        self.root = tmp_path/'mem'; (self.root/'exports').mkdir(parents=True)
        self.cache = self.root/'private/cache.json'
        self.declare([session(1, 'synthetic-session', 'claude'), session(2, 'synthetic-thread', 'codex'), session(3, 'orphan', 'claude')])

    def declare(self, sessions):
        export = dict(copy.deepcopy(EXPORT), sessions=sessions, observations=[], summaries=[], prompts=[],
                      totalSessions=len(sessions), totalObservations=0, totalSummaries=0, totalPrompts=0)
        (self.root/'exports/claude-mem.json').write_text(json.dumps(export))
        selection = dict(schema_version=1, claude_mem_version='15.9.0', instance='fixture-instance', export='exports/claude-mem.json')
        (self.root/'selection.json').write_text(json.dumps(selection))
        self.source = SourceSpec('local_file', 'claude-mem-export', 'v1', digest(str(self.root.resolve())),
            digest('synthetic-project'), digest(selection), identity_provenance='source_declared_id', enabled=True)
        self.manifest = self.root/'source.json'
        self.manifest.write_text(json.dumps(dict(schema_version=1, source=self.source.to_dict(), data='selection.json')))

    def correlate(self, natives=None, imported=True):
        if imported:
            assert main(['sources', 'import', '--manifest', str(self.manifest), '--project-sha256', self.source.project_sha256,
                '--scope-sha256', self.source.scope_sha256, '--cache', str(self.cache)]) == 0
        store = SourceStore(self.cache, project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256)
        natives = natives if natives is not None else [(h.source, h.store(), str(h.project.resolve())) for h in (self.claude, self.codex)]
        before = self.cache.read_bytes()
        report = correlate_native_sessions(ClaudeMemExport(self.manifest), self.source, store, natives)
        assert self.cache.read_bytes() == before
        text = json.dumps(report)
        for private in ('synthetic-session', 'synthetic-thread', 'orphan', 'fixture-instance', 'fixture-project', str(self.root)):
            assert private not in text
        assert report['profile'] == PROFILE and not public_export_allowed([report]) and report['independent_roots'] is None
        assert not (report['timestamp_or_content_matching'] or report['task_outcomes_verified'] or report['evidence_of_use'] or report['network_used'])
        for receipt in report['receipts']:
            assert (JoinReceipt.parse(receipt).status, JoinReceipt.parse(receipt).basis) == ('candidate', 'source_declaration')
        return report

    def by_status(self, report):
        return sorted((c['status'], len(c['memory_sessions']), len(c['native_sessions']), tuple(c['reasons'])) for c in report['clusters'])

    def test_exact_ids_link_each_platform_to_its_own_runtime_and_the_rest_stay_separate(self):
        report = self.correlate()
        assert self.by_status(report) == [('linked', 1, 1, ()), ('linked', 1, 1, ()), ('memory_only', 1, 0, ('no_native_match',))]
        assert len(report['receipts']) == 2 and report['counts']['clusters'] == 3

    def test_same_session_in_two_native_captures_is_still_one_cluster(self):
        one = (self.claude.source, self.claude.store(), str(self.claude.project.resolve()))
        report = self.correlate(natives=[one, one])
        linked = [c for c in report['clusters'] if c['status'] == 'linked']
        assert len(linked) == 1 and len(linked[0]['native_sessions']) == 2

    def test_same_session_in_two_same_runtime_journals_at_different_projects_stays_ambiguous(self, tmp_path):
        other = ClaudeSourceTests(); (tmp_path/'other').mkdir(); other.setUp(tmp_path/'other')
        assert other.invoke('import')[0] == 0
        natives = [(h.source, h.store(), str(h.project.resolve())) for h in (self.claude, other)]
        report = self.correlate(natives=natives)
        assert not report['receipts']
        assert [c['status'] for c in report['clusters'] if c['reasons'] == ['ambiguous_native_runtime']] == ['memory_only']
        assert not any(c['status'] == 'linked' for c in report['clusters'])

    def test_duplicates_wrong_platform_and_unstated_platform_stay_unlinked(self):
        self.declare([session(1, 'synthetic-session', 'claude'), session(2, 'synthetic-session', 'claude'),
                      session(3, 'synthetic-thread', 'claude'), session(4, 'synthetic-thread', None), session(5, 'x', 'cursor')])
        reasons = sorted(r for c in self.correlate()['clusters'] for r in c['reasons'])
        assert reasons == ['duplicate_session_id', 'duplicate_session_id', 'no_native_runtime', 'platform_runtime_mismatch', 'platform_unstated']

    def test_foreign_project_and_stale_export_bind_nothing(self):
        report = self.correlate(natives=[(self.claude.source, self.claude.store(), '/foreign/project')])
        assert not any(c['native_sessions'] and c['memory_sessions'] for c in report['clusters'])
        self.declare([session(1, 'late', 'claude')])
        stale = self.correlate(imported=False)
        assert (stale['status'], stale['reason'], stale['clusters']) == ('unknown', 'export_not_latest_checkpoint', [])

    def test_inputs_are_validated(self):
        with pytest.raises(ValueError): correlate_native_sessions(object(), self.source, None, [])
        with pytest.raises(ValueError): self.correlate(natives=[(self.claude.source, self.claude.store(), 'relative/project')])
        with pytest.raises(ValueError): self.correlate(natives=[(self.source, self.claude.store(), '/abs')])
        assert self.correlate(natives=[])['counts']['statuses'] == {'memory_only': 3}
