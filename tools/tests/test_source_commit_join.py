"""Synthetic cross-journal commit joins; matching SHAs never prove repository or outcome."""
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

if __package__:
    from .test_source_github import GitHubFixture
else:
    from test_source_github import GitHubFixture

from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.sources.commit_join import join_commit
from agent_harness_lab.sources.execution import CoverageManifest, ExecutionMetadata, RecordRef, ScanBound
from agent_harness_lab.sources.git import GitExport
from agent_harness_lab.sources.github import GitHubAPI, GitHubSelection
from agent_harness_lab.sources.graph import EvidenceGraph, JoinReceipt
from agent_harness_lab.sources.registry import Capability, Registry
from agent_harness_lab.sources.schema import EvidenceRecord, SourceSpec, digest
from agent_harness_lab.sources.store import SourceStore

SHA, OTHER = '1f'*20, '2e'*20
STAMP = '2026-01-01T00:00:00+00:00'


def record(source, kind, native, revision=None, state='present'):
    revision = revision or digest((kind, native, 'revision'))
    return EvidenceRecord(source.source_key, digest(native), revision, revision, 'development_artifact', kind, STAMP,
        provenance='native_field', capture_completeness='partial', state=state)


def git_commit(source, sha): return record(source, 'git_commit', ('git_commit', sha), digest(sha))
def hub_commit(source, repo, sha): return record(source, 'github_commit', ('github_commit', repo, sha))
def hub_repository(source, repo): return record(source, 'github_repository', ('github_repository', repo, repo))


class CommitJoinTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.git = self.git_source(42)
        self.hub = self.hub_source(42)

    def git_source(self, repo, label='checkout'):
        return SourceSpec('local_git', 'git', 'v2', digest(label), digest(('github.repository', repo)), digest(('scope', label)),
            identity_provenance='source_declared_id', enabled=True)

    def hub_source(self, repo):
        selection = GitHubSelection(repo, (), (), schema_version=5, details=('graph',), commits=(SHA,))
        return SourceSpec('api', 'github', selection.source_version, digest(('endpoint', repo)), selection.project_sha256,
            selection.sha256, identity_provenance='source_declared_id', enabled=True)

    def store(self, source):
        return SourceStore(self.root/(source.source_key+'.json'), project_sha256=source.project_sha256, scope_sha256=source.scope_sha256)

    def capture(self, source, *records, truncated=False):
        store = self.store(source)
        refs = tuple(RecordRef(r.source_sha256, r.native_id_sha256, r.revision_sha256) for r in records)
        metadata = None
        if truncated:
            metadata = ExecutionMetadata((), CoverageManifest(source.source_key, source.scope_sha256, source.scope_sha256,
                source.version, 'synthetic-v1', (ScanBound('commits', 1, 1),), gaps=('not_captured',),
                reported_completeness='partial', limit_reached=True, truncated=True))
        store.ingest(source, records, cursor_before=store.status(source)['cursor_sha256'],
            snapshot_sha256=digest([r.to_dict() for r in records]), metadata=metadata,
            graph=EvidenceGraph(source.source_key, source.project_sha256, source.scope_sha256, source.version, refs, ()))
        return store

    def join(self, git=None, hub=None, repository_id=42, sha=SHA):
        git, hub = git or self.git, hub or self.hub
        return join_commit(git, self.store(git), hub, self.store(hub), repository_id=repository_id, commit_sha=sha)

    def assertDescriptive(self, result, status, reason):
        self.assertEqual((result['status'], result['reason']), (status, reason))
        for name in ('native_freshness_verified', 'historical_interval_verified', 'task_outcomes_verified',
                'verification_authorized', 'execution_authorized', 'publication_allowed', 'source_read', 'network_used'):
            self.assertIs(result[name], False, name)
        self.assertIsNone(result['independent_roots'])
        self.assertEqual(result['repository']['git'], 'caller_declared')
        if status != 'matched': self.assertIsNone(result['receipt'])
        self.assertNotIn(SHA, json.dumps(result)); self.assertNotIn(str(self.root), json.dumps(result))

    def test_exact_sha_under_one_repository_identity_is_a_deterministic_candidate_only(self):
        self.capture(self.git, git_commit(self.git, SHA))
        self.capture(self.hub, hub_repository(self.hub, 42), hub_commit(self.hub, 42, SHA))
        journals = {p: p.read_bytes() for p in self.root.glob('*.json')}
        result = self.join()
        self.assertDescriptive(result, 'matched', 'exact_commit_identity')
        self.assertEqual(result['repository'], dict(key_sha256=digest(('github.repository', 42)),
            github='connector_checked_at_capture', git='caller_declared'))
        receipt = JoinReceipt.parse(result['receipt'])
        self.assertEqual((receipt.relation, receipt.status, receipt.basis, receipt.reason),
            ('same_commit', 'candidate', 'source_declaration', 'not_validated'))
        self.assertEqual({receipt.left.source_sha256, receipt.right.source_sha256}, {self.git.source_key, self.hub.source_key})
        self.assertEqual(self.join(), result)
        self.assertEqual({p: p.read_bytes() for p in self.root.glob('*.json')}, journals)

    def test_identical_sha_in_a_foreign_repository_stays_unlinked(self):
        fork = self.hub_source(43)
        self.capture(self.git, git_commit(self.git, SHA))
        self.capture(fork, hub_repository(fork, 43), hub_commit(fork, 43, SHA))
        for repository_id in (42, 43):
            with self.subTest(repository_id=repository_id):
                result = self.join(hub=fork, repository_id=repository_id)
                self.assertDescriptive(result, 'unlinked', 'repository_mismatch')
        other = self.git_source(43, 'unrelated-checkout')
        self.capture(other, git_commit(other, SHA))
        self.capture(self.hub, hub_repository(self.hub, 42), hub_commit(self.hub, 42, SHA))
        self.assertDescriptive(self.join(git=other), 'unlinked', 'repository_mismatch')

    def test_force_pushed_commits_missing_from_the_current_checkpoint_are_stale(self):
        self.capture(self.git, git_commit(self.git, SHA))
        self.capture(self.hub, hub_repository(self.hub, 42), hub_commit(self.hub, 42, SHA))
        self.capture(self.git, git_commit(self.git, OTHER))
        result = self.join()
        self.assertDescriptive(result, 'stale', 'not_in_current_checkpoint')
        self.assertEqual((result['git']['observations']['git_commit'], result['github']['observations']['github_commit']), ('stale', 'current'))
        self.capture(self.git, git_commit(self.git, SHA), git_commit(self.git, OTHER))
        self.assertEqual(self.join()['status'], 'matched')
        self.capture(self.hub, hub_repository(self.hub, 42), hub_commit(self.hub, 42, OTHER))
        result = self.join()
        self.assertDescriptive(result, 'stale', 'not_in_current_checkpoint')
        self.assertEqual(result['github']['observations']['github_commit'], 'stale')
        self.assertEqual(self.join(sha=OTHER)['status'], 'matched')

    def test_tombstoned_current_revision_is_stale_not_matched(self):
        self.capture(self.git, record(self.git, 'git_commit', ('git_commit', SHA), digest(SHA), state='tombstone'))
        self.capture(self.hub, hub_repository(self.hub, 42), hub_commit(self.hub, 42, SHA))
        self.assertDescriptive(self.join(), 'stale', 'not_in_current_checkpoint')

    def test_abbreviated_and_case_variant_shas_never_join(self):
        self.capture(self.git, git_commit(self.git, SHA))
        self.capture(self.hub, hub_repository(self.hub, 42), hub_commit(self.hub, 42, SHA))
        for value in (SHA[:7], SHA[:12], SHA.upper(), SHA[:20]+SHA[20:].upper(), ' '+SHA, SHA+'\n', SHA+'00'*12, None, 7, b'1f'*20):
            with self.subTest(value=value), self.assertRaises(ValueError): self.join(sha=value)
        upper = self.git_source(42, 'upper-checkout')
        self.capture(upper, git_commit(upper, SHA.upper()))
        result = self.join(git=upper)
        self.assertDescriptive(result, 'unknown', 'commit_unobserved')
        self.assertEqual(result['git']['observations']['git_commit'], 'unobserved')

    def test_missing_repository_identity_is_unknown_never_a_link(self):
        self.capture(self.git, git_commit(self.git, SHA))
        self.capture(self.hub, hub_commit(self.hub, 42, SHA))
        result = self.join(repository_id=None)
        self.assertDescriptive(result, 'unknown', 'repository_identity_missing')
        self.assertIsNone(result['github']); self.assertIsNone(result['repository']['key_sha256'])
        result = self.join()
        self.assertDescriptive(result, 'unknown', 'repository_identity_unobserved')
        self.assertEqual(result['repository']['github'], 'unknown')
        for repository_id in (0, -1, True, '42', 42.0):
            with self.subTest(repository_id=repository_id), self.assertRaises(ValueError): self.join(repository_id=repository_id)

    def test_absent_checkpoints_reference_only_and_truncated_absence_remain_unknown(self):
        self.assertDescriptive(self.join(), 'unknown', 'checkpoint_unavailable')
        self.capture(self.hub, hub_repository(self.hub, 42), hub_commit(self.hub, 42, SHA))
        self.assertDescriptive(self.join(), 'unknown', 'checkpoint_unavailable')
        self.capture(self.git, record(self.git, 'git_object_reference', ('git_object_reference', digest(SHA))))
        self.assertDescriptive(self.join(), 'unknown', 'commit_reference_only')
        truncated = self.git_source(42, 'truncated-checkout')
        self.capture(truncated, git_commit(truncated, OTHER), truncated=True)
        self.assertDescriptive(self.join(git=truncated), 'unknown', 'capture_truncated')
        plain = self.git_source(42, 'plain-checkout')
        self.capture(plain, git_commit(plain, OTHER))
        self.assertDescriptive(self.join(git=plain), 'unknown', 'commit_unobserved')

    def test_sources_must_be_the_declared_provider_kinds(self):
        for git, hub in ((self.hub, self.hub), (self.git, self.git), (replace(self.git, kind='local_file'), self.hub)):
            with self.subTest(kind=git.kind+hub.kind), self.assertRaises(ValueError): self.join(git=git, hub=hub)

    def test_join_reports_are_private_even_with_public_provenance(self):
        self.capture(self.git, git_commit(self.git, SHA))
        self.capture(self.hub, hub_repository(self.hub, 42), hub_commit(self.hub, 42, SHA))
        provenance = dict(source_kind='synthetic', approved_publication=True, source_ref='fixture', derived_from_private=False)
        for report in (self.join(), {**self.join(), 'publication_allowed': True}, dict(provenance=provenance, analysis=dict(profile='cross-journal-commit-join-v1'))):
            with self.subTest(status=report.get('status')):
                self.assertFalse(public_export_allowed([{**report, 'provenance': provenance}]))


@unittest.skipUnless(Path('/usr/bin/git').is_file(), 'explicit system Git is required')
class NativeCommitJoinTests(GitHubFixture, unittest.TestCase):
    def setUp(self):
        raw = subprocess.check_output(['/usr/bin/git', '--version'], env={'PATH': '/usr/bin:/bin'})
        match = re.match(rb'git version 2\.(\d+)\.', raw)
        if not match or int(match[1]) < 43: self.skipTest('Git 2.43+ in major version 2 is required')
        super().setUp()
        fixtures = Path.home()/'dev/.worktrees'; fixtures.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix='ahl-git-fixture-', dir=fixtures); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name); repo = self.root/'repo'; repo.mkdir()
        env = dict(PATH='/usr/bin:/bin', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
            GIT_AUTHOR_NAME='Synthetic', GIT_AUTHOR_EMAIL='synthetic@example.invalid', GIT_COMMITTER_NAME='Synthetic',
            GIT_COMMITTER_EMAIL='synthetic@example.invalid', GIT_AUTHOR_DATE='2026-01-01T00:00:00Z', GIT_COMMITTER_DATE='2026-01-01T00:00:00Z')
        def git(*arguments):
            return subprocess.check_output(['/usr/bin/git', '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgsign=false',
                '-c', 'maintenance.auto=false', *arguments], cwd=repo, env=env, stderr=subprocess.DEVNULL).decode().strip()
        git('init', '-b', 'main'); (repo/'AGENTS.md').write_text('SYNTHETIC_PRIVATE_INSTRUCTION\n')
        git('add', '--all'); git('commit', '-m', 'SYNTHETIC_PRIVATE_MESSAGE')
        self.sha = git('rev-parse', 'HEAD')
        selection = dict(schema_version=1, checkout='repo', git_dir='repo/.git', common_dir='repo/.git', harness_paths=['AGENTS.md'], max_commits=10)
        (self.root/'selection.json').write_text(json.dumps(selection))
        self.selection = GitHubSelection(42, (), (), schema_version=5, details=('graph',), commits=(self.sha,))
        self.git = SourceSpec('local_git', 'git', 'v2', digest(str(self.root.resolve())), self.selection.project_sha256,
            digest(selection), identity_provenance='source_declared_id', enabled=True)
        (self.root/'source.json').write_text(json.dumps(dict(schema_version=1, source=self.git.to_dict(), data='selection.json')))
        self.source = replace(self.source, version=self.selection.source_version, scope_sha256=self.selection.sha256)
        detail = {**self.commit, 'sha': self.sha, 'url': self.base+'/commits/'+self.sha, 'parents': [],
            'commit': {**self.commit['commit'], 'tree': dict(sha='e'*40), 'comment_count': 0},
            'stats': dict(additions=0, deletions=0, total=0), 'files': []}
        self.routes['/commits/'+self.sha+'?per_page=100&page=1'] = detail
        self.routes['/commits/'+self.sha+'/comments?per_page=100&page=1'] = []

    def test_built_in_reader_identities_join_only_the_exact_captured_commit(self):
        stores = {}
        for source, connector in ((self.git, GitExport(self.root/'source.json')), (self.source, GitHubAPI(self.base, self.selection, max_pages=40))):
            registry = Registry(); registry.register(Capability(source.kind, source.provider, source.version,
                ('contextual_assertion', 'development_artifact')), connector)
            stores[source.kind] = SourceStore(self.root/(source.kind+'.json'), project_sha256=source.project_sha256, scope_sha256=source.scope_sha256)
            registry.ingest(source, stores[source.kind], project_sha256=source.project_sha256, scope_sha256=source.scope_sha256)
        join = lambda **options: join_commit(self.git, stores['local_git'], self.source, stores['api'], **{'repository_id': 42, 'commit_sha': self.sha, **options})
        result = join()
        self.assertEqual((result['status'], result['repository']['github']), ('matched', 'connector_checked_at_capture'))
        self.assertEqual(JoinReceipt.parse(result['receipt']).status, 'candidate')
        self.assertEqual(join(repository_id=43)['status'], 'unlinked')
        self.assertEqual(join(commit_sha='0'*40)['status'], 'unknown')
        with self.assertRaises(ValueError): join(commit_sha=self.sha[:12])
        self.assertNotIn(self.sha, json.dumps(result)); self.assertNotIn('SYNTHETIC_PRIVATE', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
