"""Synthetic cross-journal commit joins; matching SHAs never prove repository or outcome."""
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import subprocess
import unittest

import pytest

from tests.support.unittest_cleanups import run_cleanups
from tests.unit.sources.test_source_github import GitHubFixture

from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.sources.commit_join import join_commit, join_pull_commit
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
def hub_pull(source, repo, pull, head, **state): return record(source, 'github_pull', ('github_pull', repo, pull), digest(('pull', head)), **state)
def ref(r): return RecordRef(r.source_sha256, r.native_id_sha256, r.revision_sha256)
def edge(relation, left, right): return JoinReceipt(ref(left), ref(right), relation, 'github.native', 'github-claims-v1',
    (ref(left), ref(right)), basis='native_fields')


class CommitJoinFixture:
    @pytest.fixture(autouse=True)
    def _journals(self, tmp_path):
        self.root = tmp_path
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

    def capture(self, source, *records, truncated=False, joins=()):
        store = self.store(source)
        refs = tuple(RecordRef(r.source_sha256, r.native_id_sha256, r.revision_sha256) for r in records)
        metadata = None
        if truncated:
            metadata = ExecutionMetadata((), CoverageManifest(source.source_key, source.scope_sha256, source.scope_sha256,
                source.version, 'synthetic-v1', (ScanBound('commits', 1, 1),), gaps=('not_captured',),
                reported_completeness='partial', limit_reached=True, truncated=True))
        store.ingest(source, records, cursor_before=store.status(source)['cursor_sha256'],
            snapshot_sha256=digest([r.to_dict() for r in records]), metadata=metadata,
            graph=EvidenceGraph(source.source_key, source.project_sha256, source.scope_sha256, source.version, refs, tuple(joins)))
        return store

    def join(self, git=None, hub=None, repository_id=42, sha=SHA):
        git, hub = git or self.git, hub or self.hub
        return join_commit(git, self.store(git), hub, self.store(hub), repository_id=repository_id, commit_sha=sha)

    def assertDescriptive(self, result, status, reason):
        assert (result['status'], result['reason']) == (status, reason)
        for name in ('native_freshness_verified', 'historical_interval_verified', 'task_outcomes_verified',
                'verification_authorized', 'execution_authorized', 'publication_allowed', 'source_read', 'network_used'):
            assert result[name] is False, name
        assert result['independent_roots'] is None
        assert result['repository']['git'] == 'caller_declared'
        if status != 'matched': assert not (result.get('receipt') or result.get('receipts'))
        assert SHA not in json.dumps(result); assert str(self.root) not in json.dumps(result)



class TestCommitJoin(CommitJoinFixture):
    def test_exact_sha_under_one_repository_identity_is_a_deterministic_candidate_only(self):
        self.capture(self.git, git_commit(self.git, SHA))
        self.capture(self.hub, hub_repository(self.hub, 42), hub_commit(self.hub, 42, SHA))
        journals = {p: p.read_bytes() for p in self.root.glob('*.json')}
        result = self.join()
        self.assertDescriptive(result, 'matched', 'exact_commit_identity')
        assert (result['repository'] == dict(key_sha256=digest(('github.repository', 42)),
            github='journal_declared_current', git='caller_declared'))
        receipt = JoinReceipt.parse(result['receipt'])
        assert (receipt.relation, receipt.status, receipt.basis, receipt.reason) == ('same_commit', 'candidate', 'source_declaration', 'not_validated')
        assert {receipt.left.source_sha256, receipt.right.source_sha256} == {self.git.source_key, self.hub.source_key}
        assert self.join() == result
        assert {p: p.read_bytes() for p in self.root.glob('*.json')} == journals

    @pytest.mark.parametrize('repository_id', [pytest.param(42, id='declared_repository'), pytest.param(43, id='fork_repository')])
    def test_identical_sha_in_a_foreign_repository_stays_unlinked(self, repository_id):
        fork = self.hub_source(43)
        self.capture(self.git, git_commit(self.git, SHA))
        self.capture(fork, hub_repository(fork, 43), hub_commit(fork, 43, SHA))
        result = self.join(hub=fork, repository_id=repository_id)
        self.assertDescriptive(result, 'unlinked', 'repository_identity_not_shared')

    def test_identical_sha_in_an_unrelated_checkout_stays_unlinked(self):
        other = self.git_source(43, 'unrelated-checkout')
        self.capture(other, git_commit(other, SHA))
        self.capture(self.hub, hub_repository(self.hub, 42), hub_commit(self.hub, 42, SHA))
        self.assertDescriptive(self.join(git=other), 'unlinked', 'repository_identity_not_shared')

    def test_force_pushed_commits_missing_from_the_current_checkpoint_are_stale(self):
        self.capture(self.git, git_commit(self.git, SHA))
        self.capture(self.hub, hub_repository(self.hub, 42), hub_commit(self.hub, 42, SHA))
        self.capture(self.git, git_commit(self.git, OTHER))
        result = self.join()
        self.assertDescriptive(result, 'stale', 'not_in_current_checkpoint')
        assert (result['git']['observations']['git_commit'], result['github']['observations']['github_commit']) == ('stale', 'current')
        self.capture(self.git, git_commit(self.git, SHA), git_commit(self.git, OTHER))
        assert self.join()['status'] == 'matched'
        self.capture(self.hub, hub_repository(self.hub, 42), hub_commit(self.hub, 42, OTHER))
        result = self.join()
        self.assertDescriptive(result, 'stale', 'not_in_current_checkpoint')
        assert result['github']['observations']['github_commit'] == 'stale'
        assert self.join(sha=OTHER)['status'] == 'matched'

    def test_tombstoned_current_revision_is_stale_not_matched(self):
        self.capture(self.git, record(self.git, 'git_commit', ('git_commit', SHA), digest(SHA), state='tombstone'))
        self.capture(self.hub, hub_repository(self.hub, 42), hub_commit(self.hub, 42, SHA))
        self.assertDescriptive(self.join(), 'stale', 'not_in_current_checkpoint')

    @pytest.mark.parametrize('value', [
        pytest.param(SHA[:7], id='short'), pytest.param(SHA[:12], id='abbreviated'), pytest.param(SHA.upper(), id='uppercase'),
        pytest.param(SHA[:20]+SHA[20:].upper(), id='mixed_case'), pytest.param(' '+SHA, id='leading_space'),
        pytest.param(SHA+'\n', id='trailing_newline'), pytest.param(SHA+'00'*12, id='overlong'), pytest.param(None, id='none'),
        pytest.param(7, id='integer'), pytest.param(b'1f'*20, id='bytes')])
    def test_abbreviated_and_case_variant_shas_never_join(self, value):
        self.capture(self.git, git_commit(self.git, SHA))
        self.capture(self.hub, hub_repository(self.hub, 42), hub_commit(self.hub, 42, SHA))
        with pytest.raises(ValueError): self.join(sha=value)

    def test_an_uppercase_commit_in_the_checkout_is_unobserved(self):
        self.capture(self.git, git_commit(self.git, SHA))
        self.capture(self.hub, hub_repository(self.hub, 42), hub_commit(self.hub, 42, SHA))
        upper = self.git_source(42, 'upper-checkout')
        self.capture(upper, git_commit(upper, SHA.upper()))
        result = self.join(git=upper)
        self.assertDescriptive(result, 'unknown', 'commit_unobserved')
        assert result['git']['observations']['git_commit'] == 'unobserved'

    def test_missing_repository_identity_is_unknown_never_a_link(self):
        self.capture(self.git, git_commit(self.git, SHA))
        self.capture(self.hub, hub_commit(self.hub, 42, SHA))
        result = self.join(repository_id=None)
        self.assertDescriptive(result, 'unknown', 'repository_identity_missing')
        assert result['github'] is None; assert result['repository']['key_sha256'] is None
        result = self.join()
        self.assertDescriptive(result, 'unknown', 'repository_identity_unobserved')
        assert result['repository']['github'] == 'unknown'

    @pytest.mark.parametrize('repository_id', [
        pytest.param(0, id='zero'), pytest.param(-1, id='negative'), pytest.param(True, id='boolean'),
        pytest.param('42', id='string'), pytest.param(42.0, id='float')])
    def test_invalid_repository_identities_are_rejected(self, repository_id):
        self.capture(self.git, git_commit(self.git, SHA))
        self.capture(self.hub, hub_commit(self.hub, 42, SHA))
        with pytest.raises(ValueError): self.join(repository_id=repository_id)

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

    @pytest.mark.parametrize('swap', ['hub_for_git', 'git_for_hub', 'local_file_for_git'])
    def test_sources_must_be_the_declared_provider_kinds(self, swap):
        git, hub = {'hub_for_git': (self.hub, self.hub), 'git_for_hub': (self.git, self.git),
                    'local_file_for_git': (replace(self.git, kind='local_file'), self.hub)}[swap]
        with pytest.raises(ValueError): self.join(git=git, hub=hub)

    @pytest.mark.parametrize('kind', ['report', 'forged_publication_flag', 'profile_only'])
    def test_join_reports_are_private_even_with_public_provenance(self, kind):
        self.capture(self.git, git_commit(self.git, SHA))
        self.capture(self.hub, hub_repository(self.hub, 42), hub_commit(self.hub, 42, SHA))
        provenance = dict(source_kind='synthetic', approved_publication=True, source_ref='fixture', derived_from_private=False)
        report = {'report': lambda: self.join(), 'forged_publication_flag': lambda: {**self.join(), 'publication_allowed': True},
                  'profile_only': lambda: dict(provenance=provenance, analysis=dict(profile='cross-journal-commit-join-v1'))}[kind]()
        assert not public_export_allowed([{**report, 'provenance': provenance}])


class TestPullCommitJoin(CommitJoinFixture):
    """Commit roles inside one GitHub PR; a moved head is superseded, never current."""
    PULL = 81

    def pull_join(self, git=None, hub=None, repository_id=42, pull_id=PULL, sha=SHA):
        git, hub = git or self.git, hub or self.hub
        return join_pull_commit(git, self.store(git), hub, self.store(hub), repository_id=repository_id, pull_id=pull_id, commit_sha=sha)

    def capture_pull(self, source, head, members, repo=42, pull_id=PULL, relations=('pr_head_commit',), extra=(), **state):
        pull = hub_pull(source, repo, pull_id, head, **state)
        commits = {sha: hub_commit(source, repo, sha) for sha in dict.fromkeys((*members, *extra))}
        joins = [edge('commit_on', commits[sha], pull) for sha in members]
        joins += [edge(relation, pull, commits[head]) for relation in relations if head in commits]
        return self.capture(source, hub_repository(source, repo), pull, *commits.values(), joins=joins)

    def assertRoles(self, result, status, reason, *, none=False, **roles):
        self.assertDescriptive(result, status, reason)
        assert 'receipt' not in result
        expected = dict(head='unobserved', base='unobserved', merge_result='unobserved', test_merge='unobserved', member='unobserved')
        assert result['roles'] == (None if none else {**expected, **roles})
        if status != 'matched': assert result['receipts'] == []
        assert result['profile'] == 'cross-journal-pull-commit-join-v1'

    def test_current_head_and_membership_emit_candidate_receipts_only(self):
        self.capture(self.git, git_commit(self.git, SHA))
        self.capture_pull(self.hub, SHA, (SHA,))
        journals = {p: p.read_bytes() for p in self.root.glob('*.json')}
        result = self.pull_join()
        self.assertRoles(result, 'matched', 'exact_pull_commit_role', head='current', member='current')
        receipts = [JoinReceipt.parse(r) for r in result['receipts']]
        assert [r.relation for r in receipts] == ['pr_head_commit', 'commit_on']
        for receipt in receipts:
            assert (receipt.status, receipt.basis, receipt.namespace) == ('candidate', 'source_declaration', 'git-github.pull-commit')
            assert {receipt.left.source_sha256, receipt.right.source_sha256} == {self.git.source_key, self.hub.source_key}
        assert result['pull_id_sha256'] == digest(('github_pull', 42, self.PULL))
        assert self.pull_join() == result
        assert {p: p.read_bytes() for p in self.root.glob('*.json')} == journals

    def test_advanced_head_is_superseded_while_membership_stays_current(self):
        self.capture(self.git, git_commit(self.git, SHA), git_commit(self.git, OTHER))
        self.capture_pull(self.hub, SHA, (SHA,))
        self.capture_pull(self.hub, OTHER, (SHA, OTHER))
        result = self.pull_join()
        self.assertRoles(result, 'matched', 'exact_pull_commit_role', head='superseded', member='current')
        assert [JoinReceipt.parse(r).relation for r in result['receipts']] == ['commit_on']
        self.assertRoles(self.pull_join(sha=OTHER), 'matched', 'exact_pull_commit_role', head='current', member='current')

    def test_force_pushed_head_is_stale_with_no_current_role(self):
        self.capture(self.git, git_commit(self.git, SHA), git_commit(self.git, OTHER))
        self.capture_pull(self.hub, SHA, (SHA,))
        self.capture_pull(self.hub, OTHER, (OTHER,))
        self.assertRoles(self.pull_join(), 'stale', 'not_in_current_checkpoint', head='superseded', member='superseded')
        self.capture_pull(self.hub, OTHER, (OTHER,), extra=(SHA,))
        self.assertRoles(self.pull_join(), 'stale', 'pull_role_superseded', head='superseded', member='superseded')

    @pytest.mark.parametrize(('relation', 'role'), [
        pytest.param('pr_merge_commit', 'merge_result', id='merge_result'),
        pytest.param('pr_test_merge_commit', 'test_merge', id='test_merge'), pytest.param('pr_base_commit', 'base', id='base')])
    def test_merge_result_test_merge_and_base_roles_stay_distinct(self, relation, role):
        self.capture(self.git, git_commit(self.git, SHA))
        hub = replace(self.hub, scope_sha256=digest(('scope', role)))
        self.capture_pull(hub, SHA, (), relations=(relation,), extra=(SHA,))
        result = self.pull_join(hub=hub)
        self.assertRoles(result, 'matched', 'exact_pull_commit_role', **{role: 'current'})
        assert [JoinReceipt.parse(r).relation for r in result['receipts']] == [relation]

    def test_foreign_repository_and_other_pull_identities_never_join(self):
        fork = self.hub_source(43)
        self.capture(self.git, git_commit(self.git, SHA))
        self.capture_pull(fork, SHA, (SHA,), repo=43)
        self.assertRoles(self.pull_join(hub=fork), 'unlinked', 'repository_identity_not_shared', none=True)
        self.capture_pull(self.hub, SHA, (SHA,))
        self.assertRoles(self.pull_join(pull_id=82), 'unknown', 'pull_unobserved', none=True)
        self.assertRoles(self.pull_join(repository_id=None), 'unknown', 'repository_identity_missing', none=True)

    @pytest.mark.parametrize('pull_id', [
        pytest.param(0, id='zero'), pytest.param(-1, id='negative'), pytest.param(True, id='boolean'),
        pytest.param('81', id='string'), pytest.param(81.0, id='float'), pytest.param(None, id='none')])
    def test_invalid_pull_identities_are_rejected(self, pull_id):
        self.capture(self.git, git_commit(self.git, SHA))
        self.capture_pull(self.hub, SHA, (SHA,))
        with pytest.raises(ValueError): self.pull_join(pull_id=pull_id)

    def test_edges_of_another_pull_never_become_current_or_superseded_roles(self):
        self.capture(self.git, git_commit(self.git, SHA))
        def checkpoint(source, *pulls):
            commits = {sha: hub_commit(source, 42, sha) for sha in (SHA, OTHER)}
            records = {pull_id: hub_pull(source, 42, pull_id, head) for pull_id, head in pulls}
            joins = [j for pull_id, head in pulls for j in (edge('pr_head_commit', records[pull_id], commits[head]),
                edge('commit_on', commits[head], records[pull_id]))]
            self.capture(source, hub_repository(source, 42), *records.values(), *commits.values(), joins=joins)
        shared = replace(self.hub, scope_sha256=digest(('scope', 'shared')))
        checkpoint(shared, (self.PULL, OTHER), (82, SHA))
        self.assertRoles(self.pull_join(hub=shared), 'unknown', 'pull_role_unobserved')
        self.assertRoles(self.pull_join(hub=shared, pull_id=82), 'matched', 'exact_pull_commit_role', head='current', member='current')
        earlier = replace(self.hub, scope_sha256=digest(('scope', 'earlier')))
        checkpoint(earlier, (82, SHA))
        checkpoint(earlier, (self.PULL, OTHER))
        self.assertRoles(self.pull_join(hub=earlier), 'unknown', 'pull_role_unobserved')

    def test_missing_edges_dropped_and_ambiguous_pulls_remain_unknown_or_stale(self):
        self.capture(self.git, git_commit(self.git, SHA))
        self.capture_pull(self.hub, SHA, (), relations=(), extra=(SHA,))
        self.assertRoles(self.pull_join(), 'unknown', 'pull_role_unobserved')
        self.capture(self.hub, hub_repository(self.hub, 42), hub_commit(self.hub, 42, SHA))
        self.assertRoles(self.pull_join(), 'stale', 'pull_not_in_current_checkpoint')
        ambiguous = replace(self.hub, scope_sha256=digest(('scope', 'ambiguous')))
        self.capture(ambiguous, hub_repository(ambiguous, 42), hub_commit(ambiguous, 42, SHA),
            hub_pull(ambiguous, 42, self.PULL, SHA), hub_pull(ambiguous, 42, self.PULL, OTHER))
        result = self.pull_join(hub=ambiguous)
        self.assertDescriptive(result, 'unknown', 'ambiguous_pull_revision')
        assert result['roles'] is None

    def test_pull_join_reports_are_private_even_with_public_provenance(self):
        self.capture(self.git, git_commit(self.git, SHA))
        self.capture_pull(self.hub, SHA, (SHA,))
        provenance = dict(source_kind='synthetic', approved_publication=True, source_ref='fixture', derived_from_private=False)
        for report in (self.pull_join(), dict(provenance=provenance, analysis=dict(profile='cross-journal-pull-commit-join-v1'))):
            assert not public_export_allowed([{**report, 'provenance': provenance}])


class GitHubDriver(GitHubFixture, unittest.TestCase):
    """Drives GitHubFixture by hand; it needs TestCase assertions and addCleanup, released with run_cleanups."""
    def runTest(self): pass


@pytest.fixture
def github_driver():
    driver = GitHubDriver(); driver.setUp()
    yield driver
    run_cleanups(driver)


@pytest.mark.skipif(not Path('/usr/bin/git').is_file(), reason='explicit system Git is required')
class TestNativeCommitJoin:
    @pytest.fixture(autouse=True)
    def _native(self, tmp_path, github_driver):
        raw = subprocess.check_output(['/usr/bin/git', '--version'], env={'PATH': '/usr/bin:/bin'})
        match = re.match(rb'git version 2\.(\d+)\.', raw)
        if not match or int(match[1]) < 43: pytest.skip('Git 2.43+ in major version 2 is required')
        self.f = github_driver
        self.checkout(self.f, tmp_path)

    def checkout(self, f, tmp_path):
        f.root = tmp_path; repo = f.root/'repo'; repo.mkdir()
        env = dict(PATH='/usr/bin:/bin', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
            GIT_AUTHOR_NAME='Synthetic', GIT_AUTHOR_EMAIL='synthetic@example.invalid', GIT_COMMITTER_NAME='Synthetic',
            GIT_COMMITTER_EMAIL='synthetic@example.invalid', GIT_AUTHOR_DATE='2026-01-01T00:00:00Z', GIT_COMMITTER_DATE='2026-01-01T00:00:00Z')
        def git(*arguments):
            return subprocess.check_output(['/usr/bin/git', '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgsign=false',
                '-c', 'maintenance.auto=false', *arguments], cwd=repo, env=env, stderr=subprocess.DEVNULL).decode().strip()
        git('init', '-b', 'main'); (repo/'AGENTS.md').write_text('SYNTHETIC_PRIVATE_INSTRUCTION\n')
        git('add', '--all'); git('commit', '-m', 'SYNTHETIC_PRIVATE_MESSAGE')
        f.sha = git('rev-parse', 'HEAD')
        selection = dict(schema_version=1, checkout='repo', git_dir='repo/.git', common_dir='repo/.git', harness_paths=['AGENTS.md'], max_commits=10)
        (f.root/'selection.json').write_text(json.dumps(selection))
        f.selection = GitHubSelection(42, (), (), schema_version=5, details=('graph',), commits=(f.sha,))
        f.git = SourceSpec('local_git', 'git', 'v2', digest(str(f.root.resolve())), f.selection.project_sha256,
            digest(selection), identity_provenance='source_declared_id', enabled=True)
        (f.root/'source.json').write_text(json.dumps(dict(schema_version=1, source=f.git.to_dict(), data='selection.json')))
        f.source = replace(f.source, version=f.selection.source_version, scope_sha256=f.selection.sha256)
        detail = {**f.commit, 'sha': f.sha, 'url': f.base+'/commits/'+f.sha, 'parents': [],
            'commit': {**f.commit['commit'], 'tree': dict(sha='e'*40), 'comment_count': 0},
            'stats': dict(additions=0, deletions=0, total=0), 'files': []}
        f.routes['/commits/'+f.sha+'?per_page=100&page=1'] = detail
        f.routes['/commits/'+f.sha+'/comments?per_page=100&page=1'] = []

    def test_built_in_reader_identities_join_only_the_exact_captured_commit(self):
        f = self.f
        stores = {}
        for source, connector in ((f.git, GitExport(f.root/'source.json')), (f.source, GitHubAPI(f.base, f.selection, max_pages=40))):
            registry = Registry(); registry.register(Capability(source.kind, source.provider, source.version,
                ('contextual_assertion', 'development_artifact')), connector)
            stores[source.kind] = SourceStore(f.root/(source.kind+'.json'), project_sha256=source.project_sha256, scope_sha256=source.scope_sha256)
            registry.ingest(source, stores[source.kind], project_sha256=source.project_sha256, scope_sha256=source.scope_sha256)
        join = lambda **options: join_commit(f.git, stores['local_git'], f.source, stores['api'], **{'repository_id': 42, 'commit_sha': f.sha, **options})
        result = join()
        assert (result['status'], result['repository']['github']) == ('matched', 'journal_declared_current')
        assert JoinReceipt.parse(result['receipt']).status == 'candidate'
        assert join(repository_id=43)['status'] == 'unlinked'
        assert join(commit_sha='0'*40)['status'] == 'unknown'
        with pytest.raises(ValueError): join(commit_sha=f.sha[:12])
        assert f.sha not in json.dumps(result); assert 'SYNTHETIC_PRIVATE' not in json.dumps(result)

    def test_built_in_reader_pull_edges_name_the_exact_head_commit(self):
        f = self.f
        f.selection = GitHubSelection(42, (), (8,), schema_version=5, details=('graph',), commits=(f.sha,))
        f.source = replace(f.source, version=f.selection.source_version, scope_sha256=f.selection.sha256)
        f.pull['head']['sha'] = f.sha
        f.routes['/pulls/8/commits?per_page=100&page=1'] = [{**f.commit, 'sha': f.sha, 'url': f.base+'/commits/'+f.sha, 'parents': []}]
        stores = {}
        for source, connector in ((f.git, GitExport(f.root/'source.json')), (f.source, GitHubAPI(f.base, f.selection, max_pages=40))):
            registry = Registry(); registry.register(Capability(source.kind, source.provider, source.version,
                ('contextual_assertion', 'development_artifact')), connector)
            stores[source.kind] = SourceStore(f.root/(source.kind+'.json'), project_sha256=source.project_sha256, scope_sha256=source.scope_sha256)
            registry.ingest(source, stores[source.kind], project_sha256=source.project_sha256, scope_sha256=source.scope_sha256)
        join = lambda **options: join_pull_commit(f.git, stores['local_git'], f.source, stores['api'],
            **{'repository_id': 42, 'pull_id': f.pull['id'], 'commit_sha': f.sha, **options})
        result = join()
        assert (result['status'], result['roles']['head'], result['roles']['member']) == ('matched', 'current', 'current')
        assert sorted(JoinReceipt.parse(r).relation for r in result['receipts']) == ['commit_on', 'pr_head_commit']
        assert join(pull_id=f.pull['id']+1)['reason'] == 'pull_unobserved'
        assert join(repository_id=43)['status'] == 'unlinked'
        assert f.sha not in json.dumps(result); assert 'SYNTHETIC_PRIVATE' not in json.dumps(result)
