"""Synthetic pull roles require native-shaped evidence, never heuristic claims."""
from dataclasses import replace

import pytest

from agent_harness_lab.sources.commit_join import join_pull_commit
from tests.integration.sources.test_source_commit_join import CommitJoinFixture, SHA, edge, git_commit, hub_commit, hub_pull, hub_repository


class TestPullRoleBasis(CommitJoinFixture):
    @pytest.mark.parametrize('change',[
        dict(basis='heuristic'),dict(status='conflicted',reason='conflicting_evidence'),
        dict(namespace='other.source'),dict(resolver_version='other-version'),dict(evidence=())])
    def test_non_native_edge_cannot_become_a_current_role(self,change):
        self.capture(self.git,git_commit(self.git,SHA))
        pull = hub_pull(self.hub,42,81,SHA); commit = hub_commit(self.hub,42,SHA)
        claim = replace(edge('pr_head_commit',pull,commit),**change)
        self.capture(self.hub,hub_repository(self.hub,42),pull,commit,joins=[claim])
        result = join_pull_commit(self.git,self.store(self.git),self.hub,self.store(self.hub),
                                  repository_id=42,pull_id=81,commit_sha=SHA)
        assert result['status'] == 'unknown' and result['reason'] == 'pull_role_evidence_untrusted'
        assert result['roles']['head'] == 'conflicted' and result['untrusted_roles'] == ['head'] and result['receipts'] == []
        assert not result['verification_authorized'] and not result['publication_allowed']


    def test_native_and_conflicting_same_role_do_not_emit_a_match(self):
        self.capture(self.git,git_commit(self.git,SHA))
        pull = hub_pull(self.hub,42,81,SHA); commit = hub_commit(self.hub,42,SHA)
        native = edge('pr_head_commit',pull,commit)
        conflict = replace(native,status='conflicted',reason='conflicting_evidence')
        self.capture(self.hub,hub_repository(self.hub,42),pull,commit,joins=[native,conflict])
        result = join_pull_commit(self.git,self.store(self.git),self.hub,self.store(self.hub),
                                  repository_id=42,pull_id=81,commit_sha=SHA)
        assert result['status'] == 'unknown' and result['reason'] == 'pull_role_evidence_untrusted'
        assert result['untrusted_roles'] == ['head'] and result['receipts'] == []

    def test_historical_heuristic_does_not_invent_a_superseded_role(self):
        self.capture(self.git,git_commit(self.git,SHA))
        pull = hub_pull(self.hub,42,81,SHA); commit = hub_commit(self.hub,42,SHA)
        claim = replace(edge('pr_head_commit',pull,commit),basis='heuristic')
        self.capture(self.hub,hub_repository(self.hub,42),pull,commit,joins=[claim])
        self.capture(self.hub,hub_repository(self.hub,42),pull,commit)
        result = join_pull_commit(self.git,self.store(self.git),self.hub,self.store(self.hub),
                                  repository_id=42,pull_id=81,commit_sha=SHA)
        assert result['status'] == 'unknown' and result['reason'] == 'pull_role_unobserved'
        assert result['roles']['head'] == 'unobserved' and result['prior_untrusted_roles'] == ['head']
        assert result['receipts'] == []
