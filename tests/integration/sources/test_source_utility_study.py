"""Preregistered cross-source utility study: a frozen case set, a session-only baseline and no claim beyond the fixture."""
from copy import deepcopy

import pytest

from tests.integration.sources.test_source_link_study import AdjudicatedControls
from tests.unit.sources.test_source_ci_leads import BEFORE, AFTER, CHECKS, STATUSES, CiLeadsFixture

from agent_harness_lab.sources.ci_leads import merged_pull_failing_ci_leads
from agent_harness_lab.sources.utility_study import evaluate, register

TRUTH = dict(link='positive', no_link='negative', ambiguous='ambiguous')


def case(case_id, truth, *, kind='join', visible=False, needed=('git_checkout', 'github')):
    return dict(case_id=case_id, kind=kind, truth=truth, session_visible=visible, sources_needed=list(needed))


def seen(case_id, reported, sources=(), read=0): return dict(case_id=case_id, reported=reported, sources_seen=list(sources), records_read=read)


def study(cases, baseline, cross, **options):
    prereg = register(cases, **options)
    return prereg, evaluate(prereg, dict(session_only=baseline, cross_source=cross), preregistered_sha256=prereg['preregistration_sha256'])


def synthetic(visible=5, needing=10, negatives=40, cross_misses=(), false_positives=()):
    """A large pure-scorer fixture: the baseline finds only session-visible signals, the cross-source arm finds every real one."""
    cases = [case(f'v{i}', 'positive', visible=True, needed=('session',)) for i in range(visible)]
    cases += [case(f'p{i}', 'positive', kind='contradiction') for i in range(needing)]
    cases += [case(f'n{i}', 'negative') for i in range(negatives)]
    base = [seen(c['case_id'], 'positive' if c['session_visible'] else 'not_positive', c['sources_needed'][:1] if c['session_visible'] else ['git_checkout'], 1) for c in cases]
    cross = [seen(c['case_id'], 'positive' if (c['truth'] == 'positive' and c['case_id'] not in cross_misses) or c['case_id'] in false_positives
        else 'not_positive', ['session'] if c['session_visible'] else ['git_checkout', 'github'], 3) for c in cases]
    return cases, base, cross


def test_enough_negative_controls_and_a_real_gain_support_the_fixture_only_verdict():
    _, result = study(*synthetic())
    assert (result['verdict'], result['reasons']) == ('supported_on_fixture', [])
    cross, base = result['arms']['cross_source'], result['arms']['session_only']
    assert (base['needs_cross_source']['recall']['value'], cross['needs_cross_source']['recall']['value']) == (0.0, 1.0)
    assert (base['session_visible']['recall']['value'], cross['session_visible']['recall']['value']) == (1.0, 1.0)
    assert cross['all']['false_finding_rate']['wilson95'][1] <= 0.1
    assert (cross['all']['actionable_findings'], base['all']['actionable_findings']) == (15, 5)
    assert cross['join']['cases'] == 45 and cross['contradiction']['cases'] == 10
    assert (cross['source_coverage']['value'], base['source_coverage']['value']) == (1.0, 55/105)
    assert result['incremental'] == dict(recall_gain_needs_cross_source=1.0, recall_gain_session_visible=0.0, overhead_records_read=110)
    assert (cross['overhead']['per_actionable_finding'], base['overhead']['per_actionable_finding']) == (165/15, 55/5)


def test_a_false_finding_a_regression_or_no_gain_is_not_supported_and_ambiguous_promotion_is_counted():
    _, result = study(*synthetic(false_positives=('n0',)))
    assert (result['verdict'], result['reasons']) == ('not_supported', ['false_finding'])
    cases, base, cross = synthetic()
    cases.append(case('amb', 'ambiguous')); base.append(seen('amb', 'not_positive')); cross.append(seen('amb', 'positive', ['github'], 2))
    result = study(cases, base, cross)[1]
    assert result['arms']['cross_source']['all']['counts']['ambiguous_promoted'] == 1 and result['verdict'] == 'not_supported'
    _, result = study(*synthetic(cross_misses=('v0',)))
    assert (result['verdict'], result['reasons']) == ('not_supported', ['session_visible_regression'])
    _, result = study(*synthetic(cross_misses=tuple(f'p{i}' for i in range(10))))
    assert (result['verdict'], result['reasons']) == ('not_supported', ['no_recall_gain'])


def test_a_small_study_stays_inconclusive_instead_of_certifying_a_zero_false_finding_rate():
    _, result = study(*synthetic(negatives=7))
    assert (result['verdict'], result['reasons']) == ('inconclusive', ['too_few_negative_controls_for_ceiling'])
    _, result = study(*synthetic(needing=0))
    assert (result['verdict'], result['reasons']) == ('inconclusive', ['no_comparable_cases'])
    for flag in ('diagnostic_utility_claimed', 'agent_success_claimed', 'publication_allowed'): assert result[flag] is False
    assert (result['independent_roots'], result['scope']) == (None, 'preregistered_fixture_only')


def test_edited_preregistration_dropped_or_extra_cases_and_malformed_inputs_are_rejected():
    cases, base, cross = synthetic(negatives=3)
    prereg = register(cases)
    arms = dict(session_only=base, cross_source=cross)
    recorded = prereg['preregistration_sha256']
    evaluate(prereg, arms, preregistered_sha256=recorded)
    flipped = deepcopy(prereg); next(c for c in flipped['cases'] if c['truth'] == 'positive')['truth'] = 'negative'
    looser = deepcopy(prereg); looser['rule']['false_finding_upper_ceiling'] = 0.9
    for edited in (flipped, looser, dict(prereg, preregistration_sha256='0'*64)):
        with pytest.raises(ValueError, match='recorded hash'): evaluate(edited, arms, preregistered_sha256=recorded)
    with pytest.raises(ValueError, match='recorded hash'): evaluate(prereg, arms, preregistered_sha256='0'*64)
    for bad in (dict(session_only=base, cross_source=cross[1:]), dict(session_only=base, cross_source=cross+[seen('extra', 'positive')]),
            dict(session_only=base, cross_source=cross[:1]*len(cross)), dict(session_only=base), dict(session_only=base, cross_source=cross, third=[])):
        with pytest.raises(ValueError): evaluate(prereg, bad, preregistered_sha256=recorded)
    with pytest.raises(ValueError, match='reported'):
        evaluate(prereg, dict(session_only=base, cross_source=cross[:-1]+[seen(cross[-1]['case_id'], 'success')]), preregistered_sha256=recorded)


@pytest.mark.parametrize('bad', (
    [case('a', 'positive'), case('a', 'negative')], [case('', 'positive')], [case('a', 'maybe')], [case('a', 'positive', kind='other')],
    [dict(case('a', 'positive'), session_visible='yes')], [case('a', 'positive', needed=())], [case('a', 'positive', needed=('x', 'x'))],
    [dict(case_id='a', kind='join', truth='positive')], ['a']))
def test_malformed_cases_are_rejected(bad):
    with pytest.raises(ValueError): register(bad)


@pytest.mark.parametrize('ceiling', (0.0, 1.0, 1, '0.1', -0.2))
def test_the_ceiling_must_be_a_fraction_between_zero_and_one(ceiling):
    with pytest.raises(ValueError): register([case('a', 'positive')], false_finding_ceiling=ceiling)


class TestRealJoinStudy(AdjudicatedControls):
    def test_the_commit_join_resolver_against_a_session_only_baseline(self):
        outcomes = self.controls()
        cases, base, cross = [], [], []
        for name, (truth, result, git, hub) in outcomes.items():
            cases.append(case(name, TRUTH[truth]))
            checkout = self.store(git).status(git)['revisions']
            base.append(seen(name, 'not_positive', ['git_checkout'], checkout))
            cross.append(seen(name, 'positive' if result['status'] == 'matched' else 'not_positive', ['git_checkout', 'github'],
                checkout+self.store(hub).status(hub)['revisions']))
        _, result = study(cases, base, cross)
        cross_arm, base_arm = result['arms']['cross_source'], result['arms']['session_only']
        assert (base_arm['join']['counts']['tp'], cross_arm['join']['counts']) == (0, dict(tp=1, fp=0, fn=0, tn=6, ambiguous_promoted=0))
        assert (cross_arm['join']['false_finding_rate']['value'], cross_arm['source_coverage']['value']) == (0.0, 1.0)
        assert (result['verdict'], result['reasons']) == ('inconclusive', ['too_few_negative_controls_for_ceiling'])
        assert result['incremental']['overhead_records_read'] > 0


class TestRealContradictionStudy(CiLeadsFixture):
    def test_the_failing_ci_lead_resolver_against_a_session_only_baseline(self):
        def lead(checks=(), statuses=(), merged=True):
            self.pull.update(merged=merged)
            self.routes[CHECKS] = dict(total_count=len(checks), check_runs=list(checks))
            self.routes[STATUSES] = list(statuses)
            summary = self.read(max_pages=40)[0].summary
            return bool(merged_pull_failing_ci_leads(summary, repository_id=42)['leads']), len(summary['nodes'])
        runs = {
            'failing_check': ('positive', lead(checks=[self.check(1101, 'failure')])),
            'green_checks': ('negative', lead(checks=[self.check(1102, 'success')])),
            'failing_but_unmerged': ('negative', lead(checks=[self.check(1103, 'failure')], merged=False)),
            'failure_superseded': ('negative', lead(statuses=[self.status(1200, 'failure', BEFORE), self.status(1201, 'success', AFTER)])),
            'same_instant_conflict': ('ambiguous', lead(statuses=[self.status(1300, 'failure', BEFORE), self.status(1301, 'success', BEFORE)])),
            'no_ci': ('ambiguous', lead())}
        cases = [case(name, truth, kind='contradiction', needed=('github_pull', 'github_ci')) for name, (truth, _) in runs.items()]
        base = [seen(name, 'not_positive') for name in runs]
        cross = [seen(name, 'positive' if found else 'not_positive', ['github_pull', 'github_ci'], nodes) for name, (_, (found, nodes)) in runs.items()]
        _, result = study(cases, base, cross)
        cross_arm = result['arms']['cross_source']['contradiction']
        assert (cross_arm['counts'], cross_arm['precision']['value'], cross_arm['recall']['value']) == (
            dict(tp=1, fp=0, fn=0, tn=5, ambiguous_promoted=0), 1.0, 1.0)
        assert result['arms']['session_only']['contradiction']['recall']['value'] == 0.0
        assert (result['verdict'], result['reasons']) == ('inconclusive', ['too_few_negative_controls_for_ceiling'])
