"""Candidate mechanisms and repair classes for ignored instructions, and the recorded-pair screen for a repair; synthetic data only."""
import json

import pytest

from agent_harness_lab.diagnostics import instruction_adherence as adherence
from agent_harness_lab.diagnostics import instruction_repair as repair
from tests.support.adherence_fixture import SOURCE, agent, call, session, user
from tests.support.repair_fixture import (BROKEN, COMPLIANT, CONTROL, CONTROL_BROKEN, CONTROL_CLEAN, RULE, VIOLATION, arm, first, harness,
                                          judged, mechanisms, plan_for, report_for, sessions)

LONG = '\n'.join(f'- Note {n} about the build.' for n in range(210))


def one(turns, rules=RULE, **options):
    return plan_for([session('s1', turns, loaded={SOURCE: 'loaded'}, root='r1')], rules, **options)


# --- diagnosis: candidate mechanisms, never a cause ---------------------------------------------------------------

def test_a_loaded_rule_that_a_call_broke_is_a_prose_only_rule_with_competing_explanations_and_no_authority():
    plan = one(BROKEN)
    hypothesis = first(plan)['hypotheses'][0]
    assert (hypothesis['mechanism'], hypothesis['repair_class'], hypothesis['route'], hypothesis['basis']) == (
        'prose_only_rule', 'hook_or_feedback_sensor', 'hook', 'observed')
    assert hypothesis['status'] == 'hypothesis' and 'agent_error_unrelated_to_harness' in hypothesis['competing']
    assert (plan['causal_diagnosis'], plan['change_authorized'], plan['promotion_authority'], plan['authority']) == (False, False, False, 'recommendation_only')
    assert first(plan)['disposition'] == 'recommendation_only' and 'paired_evaluation' in first(plan)['requires']


def test_a_rule_that_was_not_loaded_is_a_loading_problem_and_nothing_else():
    plan = plan_for([session('s1', BROKEN, loaded={SOURCE: 'not_loaded'}, root='r1')], RULE)
    assert mechanisms(first(plan)) == ['not_in_context'] and first(plan)['hypotheses'][0]['repair_class'] == 'restore_loading'
    assert first(plan)['hypotheses'][0]['basis'] == 'observed'


def test_a_truncated_rule_is_shortened_not_reloaded():
    plan = plan_for([session('s1', BROKEN, loaded={SOURCE: 'truncated'}, root='r1')], RULE)
    row = first(plan)['hypotheses'][0]
    assert (row['mechanism'], row['repair_class'], row['basis'], row['support']) == ('not_in_context', 'shorten_dedupe_reorder', 'observed', ['loading=truncated'])


def test_an_unrecorded_loading_state_keeps_not_in_context_as_an_unexcluded_explanation_never_a_finding_of_loading():
    plan = plan_for([session('s1', BROKEN, root='r1')], RULE)
    row = next(row for row in first(plan)['hypotheses'] if row['mechanism'] == 'not_in_context')
    assert (row['basis'], row['support']) == ('not_excluded', ['loading_unrecorded'])


def test_conflicting_rules_get_only_the_conflict_repair():
    plan = plan_for([session('s1', [user('Go.'), agent('Ok.')], loaded={SOURCE: 'loaded'}, root='r1')],
                    '- Always run `task lint`.\n- Never run `task lint`.\n')
    assert [(row['mechanism'], row['repair_class']) for row in first(plan)['hypotheses']] == [('conflicting_instructions', 'resolve_conflict')]


def test_a_rule_late_in_a_long_file_is_buried():
    plan = one(BROKEN, LONG + '\n' + RULE)
    row = next(row for row in first(plan)['hypotheses'] if row['mechanism'] == 'buried_or_duplicated')
    assert (row['repair_class'], row['support']) == ('shorten_dedupe_reorder', ['late_in_long_source'])


def test_a_rule_at_the_top_of_a_long_file_is_not_called_buried():
    assert 'buried_or_duplicated' not in mechanisms(first(one(BROKEN, RULE + LONG)))


def test_the_same_rule_written_twice_is_duplicated():
    plan = one(BROKEN, RULE + '- Keep notes short.\n' + RULE)
    assert 'duplicated' in next(row for row in first(plan)['hypotheses'] if row['mechanism'] == 'buried_or_duplicated')['support']


def test_a_short_file_with_one_rule_is_not_called_buried():
    assert 'buried_or_duplicated' not in mechanisms(first(one(BROKEN)))


def test_a_hedged_rule_is_unobservable_wording():
    plan = one(BROKEN, '- Try to never run `git push --force`.\n')
    row = next(row for row in first(plan)['hypotheses'] if row['mechanism'] == 'unobservable_wording')
    assert (row['repair_class'], row['support']) == ('reword_observable', ['hedged_wording'])


def test_a_violation_deep_in_a_session_keeps_context_loss_open_without_claiming_a_compaction():
    plan = one([user('Publish the change.'), *[agent('Working.')] * repair.LONG_SESSION_TURNS, VIOLATION])
    row = next(row for row in first(plan)['hypotheses'] if row['mechanism'] == 'context_loss')
    assert (row['basis'], row['repair_class'], row['route']) == ('not_excluded', 're_ground_after_compaction', 'state')
    assert 'compaction_not_recorded' in row['support']


def test_an_early_violation_does_not_suggest_context_loss():
    assert 'context_loss' not in mechanisms(first(one(BROKEN)))


def test_an_ignored_user_prompt_gets_a_harness_level_repair_and_the_prompt_is_never_edited():
    plan = one([user('Do not edit `config/prod.yml`.'), call('Edit', path='config/prod.yml')], rules='')
    candidate = first(plan)
    assert candidate['direction'] == 'user_prompt' and mechanisms(candidate) == ['no_self_check_for_user_instruction']
    assert candidate['hypotheses'][0]['repair_class'] == 'pre_action_checklist_or_echo'
    assert 'never_edit_the_users_prompt' in candidate['guards'] and 'never_weaken_delete_or_bypass_a_safety_rule' in candidate['guards']


def test_a_restated_prompt_instruction_is_diagnosed_without_an_edit_to_the_prompt():
    said = 'Always answer in formal English prose.'
    candidate = first(one([user(said), agent('sure lol'), user(said), agent('ok')], rules=''))
    assert (candidate['finding_mechanism'], mechanisms(candidate)) == ('instruction_restated', ['no_self_check_for_user_instruction'])
    assert 'never_edit_the_users_prompt' in candidate['guards']


def test_a_global_layer_rule_requires_the_explicit_global_scope():
    rules = harness(RULE, name='GLOBAL.md', layer='global')
    report, harness_files = report_for([session('s1', BROKEN, loaded={'global:GLOBAL.md': 'loaded'}, root='r1')], rules)
    candidate = first(repair.diagnose(report, harness_files))
    assert candidate['source_layer'] == 'global' and 'global_scope_must_be_explicit' in candidate['guards']


def test_a_project_layer_rule_does_not_need_the_global_scope():
    assert 'global_scope_must_be_explicit' not in first(one(BROKEN))['guards']


def test_skill_activation_is_listed_as_not_assessed_because_the_report_carries_no_such_evidence():
    assert [row['mechanism'] for row in one(BROKEN)['unassessed']] == ['skill_not_activated']


def test_recurrence_is_copied_and_unknown_lineage_is_a_limit_not_independence():
    plan = plan_for([session('a', BROKEN, loaded={SOURCE: 'loaded'}), session('b', BROKEN, loaded={SOURCE: 'loaded'})], RULE)
    candidate = first(plan)
    assert candidate['recurrence'] == dict(sessions=2, independent_roots=0, unknown_lineage_sessions=2)
    assert 'lineage_unknown' in candidate['limits']


def test_one_independent_root_is_a_limit():
    assert 'single_independent_root' in first(one(BROKEN))['limits']


def test_two_declared_roots_lift_the_single_root_limit():
    plan = plan_for([session('a', BROKEN, loaded={SOURCE: 'loaded'}, root='r1'), session('b', BROKEN, loaded={SOURCE: 'loaded'}, root='r2')], RULE)
    assert 'single_independent_root' not in first(plan)['limits'] and 'lineage_unknown' not in first(plan)['limits']


def test_abstentions_and_unknown_captures_are_not_diagnosed():
    report, harness_files = report_for([session('s1', BROKEN, capture='unknown', loaded={SOURCE: 'loaded'}, root='r1')], RULE)
    assert repair.diagnose(report, harness_files)['candidates'] == []
    held, files = report_for([session('s1', BROKEN, loaded={SOURCE: 'loaded'}, root='r1')], RULE, admitted=())
    plan = repair.diagnose(held, files)
    assert plan['candidates'] == [] and plan['source']['abstentions'] == 1


def test_the_plan_names_no_text_path_or_session_identifier_and_is_deterministic():
    turns = [user('Do not edit `notes/zz-private-token.md`.'), call('Edit', path='notes/zz-private-token.md'), VIOLATION]
    raw = [session('id-zz-private-token', turns, loaded={SOURCE: 'loaded'}, root='root-zz-private-token')]
    first_run, second_run = (json.dumps(plan_for(raw, RULE), sort_keys=True) for _ in range(2))
    assert first_run == second_run and 'zz-private-token' not in first_run and 'git push' not in first_run and 'Never run' not in first_run


@pytest.mark.parametrize('mutate', [lambda r: dict(r, profile='other'), lambda r: {k: v for k, v in r.items() if k != 'findings'},
                                    lambda r: dict(r, findings='none')], ids=['wrong-profile', 'missing-findings', 'findings-not-a-list'])
def test_a_report_of_another_shape_is_refused(mutate):
    report, harness_files = report_for([session('s1', BROKEN, loaded={SOURCE: 'loaded'}, root='r1')], RULE)
    with pytest.raises(ValueError):
        repair.diagnose(mutate(report), harness_files)


def test_the_section_counts_repair_classes_and_carries_no_digest():
    plan = one(BROKEN)
    section = repair.section(plan)
    assert section['status'] == 'completed' and section['artifact'] == repair.ARTIFACT and section['authority'] == 'recommendation_only'
    assert section['by_repair_class'] == dict(hook_or_feedback_sensor=1) and section['candidates'] == 1
    assert first(plan)['finding_id'] not in json.dumps(section)


# --- outcomes: the grader the screen uses -------------------------------------------------------------------------

def outcomes(raw, rules=RULE):
    rows = adherence.inventory(harness(rules))[0]
    return adherence.outcomes(sessions([raw])[0], rows)


@pytest.mark.parametrize('turns, loading, capture, expected', [
    pytest.param(BROKEN, 'loaded', 'complete', 'violated', id='broken-while-loaded'),
    pytest.param(COMPLIANT, 'loaded', 'complete', 'satisfied', id='followed-while-loaded'),
    pytest.param(BROKEN, 'not_loaded', 'complete', 'unknown', id='broken-but-never-loaded'),
    pytest.param(BROKEN, 'truncated', 'complete', 'unknown', id='broken-after-truncation'),
    pytest.param(COMPLIANT, 'not_loaded', 'complete', 'unknown', id='followed-without-the-rule'),
    pytest.param(COMPLIANT, 'loaded', 'partial', 'unknown', id='followed-in-a-partial-capture'),
    pytest.param(BROKEN, 'loaded', 'unknown', 'unknown', id='unknown-capture'),
    pytest.param(BROKEN, None, 'complete', 'unknown', id='loading-unrecorded'),
])
def test_outcomes_credit_a_rule_only_when_the_agent_demonstrably_had_it(turns, loading, capture, expected):
    raw = session('s1', turns, capture=capture, loaded={SOURCE: loading} if loading else None, root='r1')
    assert outcomes(raw) == {adherence.rules.key(adherence.inventory(harness(RULE))[0][0]['rule']): expected}


def test_a_prompt_rule_is_in_force_without_any_loading_state():
    raw = session('s1', [user('Do not edit `a.txt`.'), call('Edit', path='a.txt')], root='r1')
    assert list(outcomes(raw, rules='').values()) == ['violated']


def test_the_same_rule_in_the_harness_and_the_prompt_reports_the_worst_outcome():
    raw = session('s1', [user('Never run `git push --force`.'), VIOLATION], loaded={SOURCE: 'not_loaded'}, root='r1')
    assert list(outcomes(raw).values()) == ['violated']


# --- the screen: reproduction, regression, removal ----------------------------------------------------------------

def test_a_repair_that_the_candidate_arm_follows_while_the_baseline_arm_broke_is_supported_by_the_recorded_pair_only():
    verdict = judged()
    assert verdict['verdict'] == 'supported_by_recorded_pair' and verdict['reasons'] == []
    assert (verdict['validated_improvement'], verdict['change_authorized'], verdict['promotion_authority'], verdict['authority']) == (False, False, False, 'screen_only')
    assert verdict['reproduction']['baseline']['violated'] == 5 and verdict['reproduction']['candidate']['followed'] == 5
    assert verdict['preserved']['lost'] == 0 and verdict['preserved']['paired_roots'] == 3


def test_a_repair_that_fixes_the_target_but_loses_a_preserved_task_is_rejected_and_the_loss_is_reported_apart_from_recoveries():
    kept = [f'p{n}' for n in range(3)]
    baseline = arm(kept, CONTROL_CLEAN, prefix='pb')
    candidate = arm(kept[:1], CONTROL_BROKEN, prefix='pc') + arm(kept[1:], CONTROL_CLEAN, prefix='pd')
    broken_before = arm(['p9'], CONTROL_BROKEN, prefix='pe')
    fixed_after = arm(['p9'], CONTROL_CLEAN, prefix='pf')
    verdict = judged(preserved=(baseline + broken_before, candidate + fixed_after))
    assert verdict['verdict'] == 'rejected' and 'regression' in verdict['reasons']
    assert (verdict['preserved']['lost'], verdict['preserved']['recovered']) == (1, 1)


def test_a_candidate_harness_that_drops_the_target_rule_is_rejected_even_when_its_sessions_comply():
    verdict = judged(candidate=CONTROL)
    assert verdict['verdict'] == 'rejected' and verdict['reasons'] == ['rule_removed'] and verdict['rules']['removed'] == 1


def test_dropping_an_unrelated_checkable_rule_is_a_removal_too():
    verdict = judged(candidate=RULE)
    assert verdict['verdict'] == 'rejected' and 'rule_removed' in verdict['reasons']


def test_a_candidate_that_adds_a_rule_is_not_a_removal():
    assert judged(candidate=RULE + CONTROL + '- Never run `npm publish`.\n')['verdict'] == 'supported_by_recorded_pair'


def test_a_finding_that_the_baseline_arm_does_not_reproduce_yields_a_recommendation_and_no_change():
    roots = [f'r{n}' for n in range(5)]
    verdict = judged(reproduction=(arm(roots, COMPLIANT, prefix='b'), arm(roots, COMPLIANT, prefix='c')))
    assert verdict['verdict'] == 'recommendation_only' and verdict['reasons'] == ['not_reproduced'] and verdict['change_authorized'] is False


def test_two_roots_per_arm_cannot_separate_the_arms():
    verdict = judged(reproduction=(arm(['a', 'b'], BROKEN, prefix='b'), arm(['a', 'b'], COMPLIANT, prefix='c')))
    assert verdict['verdict'] == 'inconclusive' and verdict['reasons'] == ['interval_overlap']


def test_partial_candidate_captures_are_unknown_not_followed():
    roots = [f'r{n}' for n in range(5)]
    verdict = judged(reproduction=(arm(roots, BROKEN, prefix='b'), arm(roots, COMPLIANT, prefix='c', capture='partial')))
    assert verdict['verdict'] == 'inconclusive' and verdict['reproduction']['candidate'] == dict(verdict['reproduction']['candidate'], followed=0, unknown=5)
    assert verdict['reasons'] == ['no_decided_candidate_roots']


def test_undecided_baseline_roots_are_not_counted_as_failures_that_the_repair_then_fixed():
    roots = [f'r{n}' for n in range(5)]
    baseline = arm(roots[:3], BROKEN, prefix='b') + arm(roots[3:], COMPLIANT, prefix='u', capture='partial')
    verdict = judged(reproduction=(baseline, arm(roots, COMPLIANT, prefix='c')))
    assert verdict['verdict'] == 'inconclusive' and verdict['reasons'] == ['interval_overlap']
    assert (verdict['reproduction']['baseline']['violated'], verdict['reproduction']['baseline']['unknown']) == (3, 2)


def test_a_candidate_arm_that_never_loads_the_rule_is_not_credited():
    roots = [f'r{n}' for n in range(5)]
    verdict = judged(reproduction=(arm(roots, BROKEN, prefix='b'), arm(roots, COMPLIANT, prefix='c', loaded='not_loaded')))
    assert verdict['verdict'] == 'inconclusive' and verdict['reproduction']['candidate']['unknown'] == 5


def test_sessions_with_no_declared_root_are_excluded_from_the_interval_and_counted_apart():
    verdict = judged(reproduction=(arm([None] * 5, BROKEN, prefix='b'), arm([None] * 5, COMPLIANT, prefix='c')))
    assert verdict['verdict'] == 'inconclusive' and verdict['reproduction']['baseline']['unknown_lineage_excluded'] == 5
    assert verdict['reproduction']['baseline']['roots'] == 0 and verdict['reasons'] == ['no_decided_baseline_roots']


def test_a_root_with_mixed_sessions_counts_as_violated_once():
    mixed = arm(['r0'], BROKEN, prefix='b0') + arm(['r0'], COMPLIANT, prefix='b1') + arm(['r1', 'r2', 'r3', 'r4'], BROKEN, prefix='b')
    verdict = judged(reproduction=(mixed, arm([f'r{n}' for n in range(5)], COMPLIANT, prefix='c')))
    assert verdict['reproduction']['baseline']['roots'] == 5 and verdict['reproduction']['baseline']['violated'] == 5


def test_missing_preserved_evidence_cannot_support_a_repair():
    verdict = judged(preserved=None)
    assert verdict['verdict'] == 'inconclusive' and verdict['reasons'] == ['preserved_not_supplied'] and verdict['preserved']['status'] == 'not_supplied'


def test_a_preserved_task_missing_from_the_candidate_arm_cannot_hide_a_regression():
    kept = [f'p{n}' for n in range(3)]
    verdict = judged(preserved=(arm(kept, CONTROL_CLEAN, prefix='pb'), arm(kept[:2], CONTROL_CLEAN, prefix='pc')))
    assert verdict['verdict'] == 'inconclusive' and verdict['reasons'] == ['preserved_incomplete'] and verdict['preserved']['missing_in_candidate'] == 1


def test_a_preserved_task_the_baseline_could_not_decide_is_not_a_loss_when_the_candidate_breaks_it():
    kept = ['p0']
    baseline = arm(kept, CONTROL_CLEAN, prefix='pb', capture='partial')
    verdict = judged(preserved=(baseline, arm(kept, CONTROL_BROKEN, prefix='pc')))
    assert verdict['preserved']['lost'] == 0 and verdict['preserved']['undecided'] == 1


def test_an_unknown_target_is_refused():
    with pytest.raises(ValueError, match='target'):
        judged(target='0' * 16)


def test_a_user_prompt_target_is_screened_through_the_recorded_prompt():
    prompt = [user('Do not edit `a.txt`.'), call('Edit', path='a.txt')]
    honored = [user('Do not edit `a.txt`.'), agent('Working.'), call('Edit', path='b.txt')]
    roots = [f'r{n}' for n in range(5)]
    base = sessions(arm(roots, prompt, prefix='b', loaded=None))
    ref = next(iter(adherence.prompt_rules(base[0])))
    verdict = repair.judge(ref, [], harness('- Echo the prompt constraints before each edit.\n'), reproduction=(base, sessions(arm(roots, honored, prefix='c', loaded=None))),
                           preserved=(sessions(arm(['p'], honored, prefix='pb', loaded=None)), sessions(arm(['p'], honored, prefix='pc', loaded=None))))
    assert verdict['verdict'] == 'supported_by_recorded_pair'


def test_the_verdict_names_no_text_and_is_deterministic():
    first_run, second_run = (json.dumps(judged(), sort_keys=True) for _ in range(2))
    assert first_run == second_run and 'git push' not in first_run and 'Never run' not in first_run
