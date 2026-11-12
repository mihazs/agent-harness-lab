"""Synthetic Git/Gitea pull request commit joins; server-reported membership never proves ancestry or outcome."""
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import subprocess

import pytest

from tests.integration.sources.test_source_commit_join import SHA, OTHER, STAMP, CommitJoinFixture, git_commit
from tests.unit.sources.test_source_gitea import GiteaFixture
from tests.unit.sources.test_source_transport import Response

from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.sources.commit_join import join_pull_request_commit
from agent_harness_lab.sources.gitea import GiteaAPI, GiteaSelection
from agent_harness_lab.sources.git import GitExport
from agent_harness_lab.sources.graph import JoinReceipt
from agent_harness_lab.sources.registry import Capability, Registry
from agent_harness_lab.sources.schema import EvidenceRecord, SourceSpec, digest
from agent_harness_lab.sources.store import SourceStore

BASE, OWNER, REPO, REPOSITORY, PULL, NUMBER = 'https://gitea.example.test', 'synthetic-owner', 'synthetic-repository', 42, 81, 8


def selection(**changes):
    return GiteaSelection(BASE, OWNER, REPO, ('commits', 'pulls'), pulls=(NUMBER,), schema_version=6, **changes)


class PullJoinFixture(CommitJoinFixture):
    @pytest.fixture(autouse=True)
    def _lab(self, _journals):
        self.selection = selection()
        self.git = SourceSpec('local_git', 'git', 'v2', digest('checkout'), self.selection.project_sha256, digest(('scope', 'checkout')),
            identity_provenance='source_declared_id', enabled=True)
        self.lab = SourceSpec('api', 'gitea', self.selection.source_version, digest(BASE), self.selection.project_sha256,
            self.selection.sha256, identity_provenance='source_declared_id', enabled=True)

    def lab_records(self, sha=SHA, repository=REPOSITORY, pull=PULL, number=NUMBER):
        chosen, repo = self.selection, self.selection.native_sha256(repository)
        natives = (('gitea_repository', chosen.record_sha256('gitea_repository', repo, 'repository')),
            ('gitea_pull', chosen.record_sha256('gitea_pull', repo, chosen.native_sha256(pull))),
            ('gitea_pull_commit', chosen.record_sha256('gitea_pull_commit', repo, (number, digest(sha)))))
        return tuple(EvidenceRecord(self.lab.source_key, native, digest((kind, native)), digest((kind, native)),
            'development_artifact', kind, STAMP, provenance='native_field', capture_completeness='partial') for kind, native in natives)

    def join(self, git=None, lab=None, **options):
        git, lab = git or self.git, lab or self.lab
        return join_pull_request_commit(git, self.store(git), lab, self.store(lab), **{'selection': self.selection,
            'repository_id': REPOSITORY, 'pull_id': PULL, 'number': NUMBER, 'commit_sha': SHA, **options})

    def expect(self, result, status, reason):
        assert (result['status'], result['reason']) == (status, reason)
        for name in ('native_freshness_verified', 'task_outcomes_verified', 'verification_authorized', 'execution_authorized',
                'publication_allowed', 'source_read', 'network_used'):
            assert result[name] is False, name
        assert (result['membership'], result['ancestry'], result['independent_roots']) == ('server_reported', 'unknown', None)
        if status != 'matched': assert not result['receipt']
        assert SHA not in json.dumps(result) and str(self.root) not in json.dumps(result)


class TestPullRequestCommitJoin(PullJoinFixture):
    def test_exact_reported_membership_is_a_deterministic_candidate_only(self):
        self.capture(self.git, git_commit(self.git, SHA))
        self.capture(self.lab, *self.lab_records())
        journals = {p: p.read_bytes() for p in self.root.glob('*.json')}
        result = self.join()
        self.expect(result, 'matched', 'exact_pull_request_commit_membership')
        assert (result['profile'], result['repository']['gitea']) == ('cross-journal-gitea-pull-commit-join-v1', 'journal_declared_current')
        receipt = JoinReceipt.parse(result['receipt'])
        assert (receipt.relation, receipt.status, receipt.basis, receipt.reason) == ('same_commit', 'candidate', 'source_declaration', 'not_validated')
        assert {receipt.left.source_sha256, receipt.right.source_sha256} == {self.git.source_key, self.lab.source_key}
        assert self.join() == result
        assert {p: p.read_bytes() for p in self.root.glob('*.json')} == journals
        provenance = dict(source_kind='synthetic', approved_publication=True, source_ref='fixture', derived_from_private=False)
        assert not public_export_allowed([{**result, 'provenance': provenance}])

    @pytest.mark.parametrize('options', [dict(repository_id=43), dict(pull_id=82), dict(commit_sha=OTHER)],
        ids=['other-repository', 'other-pull-object', 'other-commit'])
    def test_other_repository_pull_or_commit_is_not_joined(self, options):
        self.capture(self.git, git_commit(self.git, SHA))
        self.capture(self.lab, *self.lab_records())
        result = self.join(**options)
        assert result['status'] in ('unknown', 'stale') and not result['receipt']

    def test_foreign_git_scope_stays_unlinked(self):
        foreign = replace(self.git, project_sha256=digest('other-repository'))
        self.capture(foreign, git_commit(foreign, SHA))
        self.capture(self.lab, *self.lab_records())
        self.expect(self.join(git=foreign), 'unlinked', 'repository_identity_not_shared')

    def test_missing_records_stay_unknown(self):
        self.capture(self.git, git_commit(self.git, SHA))
        repository, pull, commit = self.lab_records()
        self.capture(self.lab, repository, pull)
        self.expect(self.join(), 'unknown', 'commit_unobserved')
        self.capture(self.lab, pull, commit)
        self.expect(self.join(), 'unknown', 'repository_identity_unobserved')
        self.capture(self.lab, repository, commit)
        self.expect(self.join(), 'unknown', 'pull_unobserved')

    def test_no_graph_or_no_local_commit_stays_unknown(self):
        self.expect(self.join(), 'unknown', 'checkpoint_unavailable')
        self.capture(self.lab, *self.lab_records())
        self.capture(self.git, git_commit(self.git, OTHER))
        self.expect(self.join(), 'unknown', 'commit_unobserved')

    def test_commit_dropped_from_the_latest_checkpoint_is_stale(self):
        self.capture(self.git, git_commit(self.git, SHA))
        repository, pull, commit = self.lab_records()
        self.capture(self.lab, repository, pull, commit)
        self.capture(self.lab, repository, pull)
        self.expect(self.join(), 'stale', 'not_in_current_checkpoint')

    @pytest.mark.parametrize('options', [dict(commit_sha=SHA[:12]), dict(commit_sha=SHA.upper()), dict(number=0), dict(pull_id=True),
        dict(repository_id=0), dict(number=9), dict(selection=replace(selection(), capture=('pulls',))),
        dict(selection=replace(selection(), schema_version=5)), dict(selection=object())],
        ids=['short-sha', 'upper-sha', 'zero-number', 'boolean-pull', 'zero-repository', 'unselected-pull', 'no-commits', 'schema-5', 'not-a-selection'])
    def test_invalid_inputs_are_rejected_before_any_journal_read(self, options):
        with pytest.raises(ValueError): self.join(**options)

    @pytest.mark.parametrize('change', [dict(scope_sha256=digest('other')), dict(version='v5'), dict(provider='gitlab')])
    def test_source_must_match_the_selection(self, change):
        with pytest.raises(ValueError): self.join(lab=replace(self.lab, **change))


@pytest.mark.skipif(not Path('/usr/bin/git').is_file(), reason='explicit system Git is required')
class TestNativePullRequestCommitJoin(GiteaFixture):
    def test_built_in_readers_join_only_the_exact_reported_commit(self, tmp_path):
        raw = subprocess.check_output(['/usr/bin/git', '--version'], env={'PATH': '/usr/bin:/bin'})
        match = re.match(rb'git version 2\.(\d+)\.', raw)
        if not match or int(match[1]) < 43: pytest.skip('Git 2.43+ in major version 2 is required')
        repo = tmp_path/'repo'; repo.mkdir()
        env = dict(PATH='/usr/bin:/bin', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
            GIT_AUTHOR_NAME='Synthetic', GIT_AUTHOR_EMAIL='synthetic@example.invalid', GIT_COMMITTER_NAME='Synthetic',
            GIT_COMMITTER_EMAIL='synthetic@example.invalid', GIT_AUTHOR_DATE='2026-01-01T00:00:00Z', GIT_COMMITTER_DATE='2026-01-01T00:00:00Z')
        def git(*arguments):
            return subprocess.check_output(['/usr/bin/git', '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgsign=false',
                '-c', 'maintenance.auto=false', *arguments], cwd=repo, env=env, stderr=subprocess.DEVNULL).decode().strip()
        git('init', '-b', 'main'); (repo/'AGENTS.md').write_text('SYNTHETIC_PRIVATE_INSTRUCTION\n')
        git('add', '--all'); git('commit', '-m', 'SYNTHETIC_PRIVATE_MESSAGE')
        sha = git('rev-parse', 'HEAD')
        chosen = replace(self.selection, schema_version=6, capture=('commits', 'pulls'), issues=())
        checkout = dict(schema_version=1, checkout='repo', git_dir='repo/.git', common_dir='repo/.git', harness_paths=['AGENTS.md'], max_commits=10)
        (tmp_path/'selection.json').write_text(json.dumps(checkout))
        local = SourceSpec('local_git', 'git', 'v2', digest(str(tmp_path.resolve())), chosen.project_sha256, digest(checkout),
            identity_provenance='source_declared_id', enabled=True)
        (tmp_path/'source.json').write_text(json.dumps(dict(schema_version=1, source=local.to_dict(), data='selection.json')))
        self.routes[self.repo_path+'/pulls/8/commits?page=1&limit=50&verification=false&files=false'] = lambda: Response(
            [dict(sha=sha, message='SYNTHETIC_PRIVATE_BODY')], headers={**self.headers, 'X-Total': '1', 'X-HasMore': 'false', 'X-Page': '1'})
        remote = replace(self.spec(chosen), version='v6')
        stores = {}
        for source, connector in ((local, GitExport(tmp_path/'source.json')), (remote, GiteaAPI(self.base, chosen))):
            registry = Registry(); registry.register(Capability(source.kind, source.provider, source.version,
                ('contextual_assertion', 'development_artifact')), connector)
            stores[source.kind] = SourceStore(tmp_path/(source.kind+'.json'), project_sha256=source.project_sha256, scope_sha256=source.scope_sha256)
            registry.ingest(source, stores[source.kind], project_sha256=source.project_sha256, scope_sha256=source.scope_sha256)
        join = lambda **options: join_pull_request_commit(local, stores['local_git'], remote, stores['api'],
            **{'selection': chosen, 'repository_id': 42, 'pull_id': 81, 'number': 8, 'commit_sha': sha, **options})
        result = join()
        assert (result['status'], result['repository']['gitea']) == ('matched', 'journal_declared_current')
        assert join(repository_id=43)['status'] == 'unknown'
        assert join(pull_id=82)['status'] == 'unknown'
        assert join(commit_sha='0'*40)['status'] == 'unknown'
        assert sha not in json.dumps(result) and 'SYNTHETIC_PRIVATE' not in json.dumps(result)
