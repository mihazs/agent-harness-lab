"""Synthetic Git/GitLab merge request commit joins; server-reported membership never proves ancestry or outcome."""
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import subprocess

import pytest

from tests.integration.sources.test_source_commit_join import SHA, OTHER, CommitJoinFixture, git_commit, record
from tests.unit.sources.test_source_gitlab import GitLabFixture

from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.sources.commit_join import join_merge_request_commit
from agent_harness_lab.sources.git import GitExport
from agent_harness_lab.sources.gitlab import GitLabAPI, GitLabSelection
from agent_harness_lab.sources.graph import JoinReceipt
from agent_harness_lab.sources.registry import Capability, Registry
from agent_harness_lab.sources.schema import SourceSpec, digest
from agent_harness_lab.sources.store import SourceStore

BASE, PATH, PROJECT, IID = 'https://gitlab.example.test', 'synthetic-group/synthetic-project', 42, 7


def selection(**changes):
    return GitLabSelection(BASE, PATH, ('commits', 'merge_requests'), merge_requests=(IID,), schema_version=9, **changes)


class MergeRequestJoinFixture(CommitJoinFixture):
    @pytest.fixture(autouse=True)
    def _lab(self, _journals):
        self.selection = selection()
        self.git = SourceSpec('local_git', 'git', 'v2', digest('checkout'), self.selection.project_sha256, digest(('scope', 'checkout')),
            identity_provenance='source_declared_id', enabled=True)
        self.lab = SourceSpec('api', 'gitlab', self.selection.source_version, digest(BASE), self.selection.project_sha256,
            self.selection.sha256, identity_provenance='source_declared_id', enabled=True)

    def lab_records(self, sha=SHA, project=PROJECT, iid=IID):
        scope = self.selection.project_identity(project)
        return (record(self.lab, 'gitlab_project', ('gitlab_project', scope, 'project')),
            record(self.lab, 'gitlab_merge_request', ('gitlab_merge_request', scope, iid)),
            record(self.lab, 'gitlab_mr_commit', ('gitlab_mr_commit', scope, (iid, digest(sha)))))

    def join(self, git=None, lab=None, **options):
        git, lab = git or self.git, lab or self.lab
        return join_merge_request_commit(git, self.store(git), lab, self.store(lab),
            **{'selection': self.selection, 'project_id': PROJECT, 'iid': IID, 'commit_sha': SHA, **options})

    def expect(self, result, status, reason):
        assert (result['status'], result['reason']) == (status, reason)
        for name in ('native_freshness_verified', 'task_outcomes_verified', 'verification_authorized', 'execution_authorized',
                'publication_allowed', 'source_read', 'network_used'):
            assert result[name] is False, name
        assert (result['membership'], result['ancestry'], result['independent_roots']) == ('server_reported', 'unknown', None)
        if status != 'matched': assert not result['receipt']
        assert SHA not in json.dumps(result) and str(self.root) not in json.dumps(result)


class TestMergeRequestCommitJoin(MergeRequestJoinFixture):
    def test_exact_reported_membership_is_a_deterministic_candidate_only(self):
        self.capture(self.git, git_commit(self.git, SHA))
        project, mr, commit = self.lab_records()
        self.capture(self.lab, project, mr, commit)
        journals = {p: p.read_bytes() for p in self.root.glob('*.json')}
        result = self.join()
        self.expect(result, 'matched', 'exact_merge_request_commit_membership')
        assert result['repository']['gitlab'] == 'journal_declared_current'
        receipt = JoinReceipt.parse(result['receipt'])
        assert (receipt.relation, receipt.status, receipt.basis, receipt.reason) == ('same_commit', 'candidate', 'source_declaration', 'not_validated')
        assert self.join() == result
        assert {p: p.read_bytes() for p in self.root.glob('*.json')} == journals
        provenance = dict(source_kind='synthetic', approved_publication=True, source_ref='fixture', derived_from_private=False)
        assert not public_export_allowed([{**result, 'provenance': provenance}])

    @pytest.mark.parametrize('change', ['project', 'sha'])
    def test_other_project_or_commit_is_not_joined(self, change):
        self.capture(self.git, git_commit(self.git, SHA))
        self.capture(self.lab, *self.lab_records())
        options = dict(project=dict(project_id=43), sha=dict(commit_sha=OTHER))[change]
        result = self.join(**options)
        assert result['status'] in ('unknown', 'stale') and not result['receipt']

    def test_foreign_git_scope_stays_unlinked(self):
        foreign = replace(self.git, project_sha256=digest('other-project'))
        self.capture(foreign, git_commit(foreign, SHA))
        self.capture(self.lab, *self.lab_records())
        self.expect(self.join(git=foreign), 'unlinked', 'repository_identity_not_shared')

    def test_missing_project_record_or_unobserved_commit_stays_unknown(self):
        self.capture(self.git, git_commit(self.git, SHA))
        project, mr, commit = self.lab_records()
        self.capture(self.lab, project, mr)
        self.expect(self.join(), 'unknown', 'commit_unobserved')
        self.capture(self.lab, mr, commit)
        self.expect(self.join(), 'unknown', 'project_identity_unobserved')

    def test_commit_dropped_from_the_latest_checkpoint_is_stale(self):
        self.capture(self.git, git_commit(self.git, SHA))
        project, mr, commit = self.lab_records()
        self.capture(self.lab, project, mr, commit)
        self.capture(self.lab, project, mr)
        self.expect(self.join(), 'stale', 'not_in_current_checkpoint')

    def test_invalid_inputs_are_rejected_before_any_journal_read(self):
        for options in (dict(commit_sha=SHA[:12]), dict(commit_sha=SHA.upper()), dict(iid=0), dict(project_id=True),
                dict(selection=replace(self.selection, capture=('merge_requests',))), dict(selection=replace(self.selection, schema_version=4)),
                dict(iid=99)):
            with pytest.raises(ValueError): self.join(**options)
        with pytest.raises(ValueError): self.join(lab=replace(self.lab, scope_sha256=digest('other')))


@pytest.mark.skipif(not Path('/usr/bin/git').is_file(), reason='explicit system Git is required')
class TestNativeMergeRequestCommitJoin(GitLabFixture):
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
        chosen = replace(self.selection, schema_version=9, capture=('commits', 'merge_requests'), issues=())
        checkout = dict(schema_version=1, checkout='repo', git_dir='repo/.git', common_dir='repo/.git', harness_paths=['AGENTS.md'], max_commits=10)
        (tmp_path/'selection.json').write_text(json.dumps(checkout))
        local = SourceSpec('local_git', 'git', 'v2', digest(str(tmp_path.resolve())), chosen.project_sha256, digest(checkout),
            identity_provenance='source_declared_id', enabled=True)
        (tmp_path/'source.json').write_text(json.dumps(dict(schema_version=1, source=local.to_dict(), data='selection.json')))
        self.routes[self.mr_path+'/commits?page=1&per_page=100'] = [dict(id=sha, committed_date=self.stamp, message='SYNTHETIC_PRIVATE_BODY')]
        remote = self.spec(chosen)
        stores = {}
        for source, connector in ((local, GitExport(tmp_path/'source.json')), (remote, GitLabAPI(self.base, chosen))):
            registry = Registry(); registry.register(Capability(source.kind, source.provider, source.version,
                ('contextual_assertion', 'development_artifact')), connector)
            stores[source.kind] = SourceStore(tmp_path/(source.kind+'.json'), project_sha256=source.project_sha256, scope_sha256=source.scope_sha256)
            registry.ingest(source, stores[source.kind], project_sha256=source.project_sha256, scope_sha256=source.scope_sha256)
        join = lambda **options: join_merge_request_commit(local, stores['local_git'], remote, stores['api'],
            **{'selection': chosen, 'project_id': 42, 'iid': 7, 'commit_sha': sha, **options})
        result = join()
        assert (result['status'], result['repository']['gitlab']) == ('matched', 'journal_declared_current')
        assert join(project_id=43)['status'] == 'unknown'
        assert join(commit_sha='0'*40)['status'] == 'unknown'
        assert sha not in json.dumps(result) and 'SYNTHETIC_PRIVATE' not in json.dumps(result)
