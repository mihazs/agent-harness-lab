"""Adjudicated synthetic link fixture: ambiguous controls must never be promoted to a link."""
import pytest

from agent_harness_lab.sources.link_study import score
from tests.integration.sources.test_source_commit_join import (
    OTHER, SHA, CommitJoinFixture, git_commit, hub_commit, hub_repository, record)
from agent_harness_lab.sources.schema import digest


class TestCommitJoinLinkStudy(CommitJoinFixture):
    def test_real_resolver_has_no_false_links_on_adjudicated_controls(self):
        self.capture(self.hub, hub_repository(self.hub, 42), hub_commit(self.hub, 42, SHA))
        fork = self.hub_source(43)
        self.capture(fork, hub_repository(fork, 43), hub_commit(fork, 43, SHA))
        outcomes = {}
        def case(name, truth, git, **kw):
            outcomes[name] = (truth, self.join(git=git, **kw)['status'])
        good = self.git_source(42, 'good'); self.capture(good, git_commit(good, SHA))
        case('exact', 'link', good)
        case('fork_repository', 'no_link', good, hub=fork, repository_id=43)
        other = self.git_source(43, 'unrelated'); self.capture(other, git_commit(other, SHA))
        case('unrelated_checkout', 'no_link', other)
        moved = self.git_source(42, 'moved'); self.capture(moved, git_commit(moved, SHA)); self.capture(moved, git_commit(moved, OTHER))
        case('force_pushed', 'no_link', moved)
        different = self.git_source(42, 'different'); self.capture(different, git_commit(different, OTHER))
        case('different_commit', 'no_link', different)
        reference = self.git_source(42, 'reference')
        self.capture(reference, record(reference, 'git_object_reference', ('git_object_reference', digest(SHA))))
        case('reference_only', 'ambiguous', reference)
        case('missing_repository_identity', 'ambiguous', good, repository_id=None)
        result = score([dict(case_id=k, truth=t, status=s) for k, (t, s) in outcomes.items()])
        assert result['counts'] == dict(tp=1, fp=0, fn=0, tn=6, ambiguous_promoted=0, ambiguous_held=2, truth_links=1, truth_non_links=6)
        assert (result['precision']['value'], result['false_link_rate']['value']) == (1.0, 0.0)
        assert result['false_link_rate']['wilson95'][1] > 0.3  # seven controls cannot certify a zero rate


def test_promoted_ambiguous_or_proximity_matches_are_counted_as_false_links():
    cases = [dict(case_id='a', truth='link', status='verified'), dict(case_id='b', truth='ambiguous', status='matched'),
             dict(case_id='c', truth='no_link', status='reviewed'), dict(case_id='d', truth='no_link', status='candidate'),
             dict(case_id='e', truth='link', status='unknown')]
    result = score(cases)
    assert (result['counts']['fp'], result['counts']['ambiguous_promoted'], result['counts']['fn']) == (2, 1, 1)
    assert result['precision']['value'] == pytest.approx(1/3) and result['false_link_rate']['value'] == pytest.approx(2/3)
    assert result['recall']['value'] == 0.5
    assert result['diagnostic_utility_claimed'] is False and result['publication_allowed'] is False


def test_empty_study_reports_unknown_rates_and_bad_cases_are_rejected():
    assert score([])['precision'] == dict(hits=0, total=0, value=None, wilson95=None)
    for bad in ([dict(case_id='a', truth='link', status='success')], [dict(case_id='a', truth='yes', status='matched')],
                [dict(case_id='a', truth='link', status='matched')]*2, [dict(case_id='a', truth='link')], ['a']):
        with pytest.raises(ValueError): score(bad)
