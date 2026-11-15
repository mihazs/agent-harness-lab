"""Revert requests, reopened tasks and steering messages as weak outcome signals on a user instruction, judged on synthetic sessions only."""
import json

import pytest

from agent_harness_lab.diagnostics import instruction_adherence as adherence
from tests.support.adherence_fixture import agent, analyze, edit, session, shell, statuses, user

PROD = 'Do not edit `config/prod.yml`.'


def only(report):
    assert len(report['findings']) == 1, report['findings']
    return report['findings'][0]


# A violation the capture shows, followed by the user's reaction: the reaction corroborates, it never replaces the evidence.

@pytest.mark.parametrize('turns,signal', [
    ([user(PROD), edit('config/prod.yml'), agent('Done.'), user('Please revert `config/prod.yml`.')], 'user_revert_requested'),
    ([user(PROD), edit('config/prod.yml'), agent('Done.'), user('Undo the change to config/prod.yml.')], 'user_revert_requested'),
    ([user(PROD), edit('config/prod.yml'), agent('Done.'), user('That is not done: `config/prod.yml` must stay as it was.')], 'user_task_reopened'),
    ([user(PROD), edit('config/prod.yml'), user('Wait, not `config/prod.yml`!')], 'user_steering_message')],
    ids=['revert-backticked', 'revert-bare-path', 'reopened', 'steered-right-after-the-call'])
def test_user_reaction_naming_the_violated_operand_is_corroboration_of_the_observed_violation(turns, signal):
    finding = only(analyze([session('s', turns)]))
    assert (finding['status'], finding['mechanism'], finding['corroboration']) == ('observed_violation', 'forbidden_action_taken', [signal])


def test_corroborations_are_listed_in_a_fixed_order_when_several_signals_follow_one_violation():
    turns = [user(PROD), edit('config/prod.yml'), user('Wait, `config/prod.yml`!'), agent('Sorry.'),
             user('Revert `config/prod.yml`, it is not done.')]
    assert only(analyze([session('s', turns)]))['corroboration'] == ['user_revert_requested', 'user_task_reopened', 'user_steering_message']


def test_reaction_that_precedes_the_violating_call_does_not_corroborate_it():
    turns = [user(PROD), agent('Ok.'), user('Revert `config/prod.yml` if you touch it.'), edit('config/prod.yml')]
    assert only(analyze([session('s', turns)]))['corroboration'] == []


# Without checkable evidence the reaction is only a possible violation, with its own mechanism.

@pytest.mark.parametrize('turns,mechanism', [
    ([user(PROD), agent('Working.'), agent('Done.'), user('Please revert `config/prod.yml`.')], 'revert_requested'),
    ([user(PROD), agent('Working.'), agent('Done.'), user('You forgot: `config/prod.yml` stays untouched.')], 'task_reopened'),
    ([user(PROD), shell('ls'), user('Wait, not `config/prod.yml`!')], 'steering_message')],
    ids=['revert', 'reopened', 'steered'])
def test_reaction_to_an_instruction_a_partial_capture_cannot_judge_is_a_possible_violation(turns, mechanism):
    finding = only(analyze([session('s', turns, capture='partial')]))
    assert (finding['status'], finding['direction'], finding['mechanism']) == ('possible_violation', 'user_prompt', mechanism)
    assert (finding['occurrences'], finding['turns']) == (1, [])


def test_possible_violation_spans_the_instruction_turn_and_the_reaction_turn():
    turns = [user(PROD), agent('Working.'), agent('Done.'), user('Please revert `config/prod.yml`.')]
    assert only(analyze([session('s', turns, capture='partial')]))['span'] == dict(first_turn=0, last_turn=3)


def test_one_finding_per_instruction_names_the_strongest_signal_and_keeps_the_others_as_corroboration():
    said = 'Never touch `config/prod.yml`.'
    turns = [user(said), shell('ls'), user('Wait, `config/prod.yml`?'), agent('Yes.'), user(said), agent('Ok.'), user('Revert `config/prod.yml`.')]
    finding = only(analyze([session('s', turns, capture='partial')]))
    assert (finding['mechanism'], finding['corroboration']) == ('revert_requested', ['user_steering_message', 'user_restated'])


# What is not a signal: a reaction that names nothing, names something else, answers a question or follows a decided rule.

@pytest.mark.parametrize('reaction', [
    'Please revert that.', 'That is not done.', 'Revert `src/other.py`.', 'You forgot the changelog.', 'Looks fine, thanks.', ''],
    ids=['names-nothing', 'reopens-nothing-named', 'other-operand', 'other-subject', 'no-cue', 'empty'])
def test_reaction_that_does_not_name_the_instructions_operand_is_no_signal(reaction):
    turns = [user(PROD), agent('Working.'), agent('Done.'), user(reaction)]
    report = analyze([session('s', turns, capture='partial')])
    assert report['findings'] == [] and report['abstentions'] == []


def test_naming_the_operand_without_a_cue_is_no_signal_after_an_agent_reply():
    turns = [user(PROD), agent('Working.'), agent('Done.'), user('Thanks for leaving `config/prod.yml` alone.')]
    assert analyze([session('s', turns, capture='partial')])['findings'] == []


def test_reply_to_an_agent_question_is_not_a_revert_request():
    turns = [user(PROD), agent('Working.'), agent('Shall I revert `config/prod.yml`?'), user('Yes, revert `config/prod.yml`.')]
    assert analyze([session('s', turns, capture='partial')])['findings'] == []


def test_complete_capture_where_the_agent_obeyed_is_no_finding_whatever_the_user_says():
    turns = [user(PROD), edit('src/app.py'), agent('Done.'), user('Revert `config/prod.yml`, it is not done.')]
    report = analyze([session('s', turns)])
    assert report['findings'] == [] and report['abstentions'] == []


def test_reaction_with_a_supersession_cue_is_a_changed_mind_not_a_signal():
    turns = [user(PROD), agent('Working.'), agent('Done.'), user('Actually, revert `config/prod.yml` to the old one.')]
    assert analyze([session('s', turns, capture='partial')])['findings'] == []


def test_signal_after_the_instruction_was_superseded_is_not_read():
    turns = [user(PROD), agent('Ok.'), user('Actually, go ahead and edit `config/prod.yml`.'), agent('Done.'), user('Revert `config/prod.yml`.')]
    assert analyze([session('s', turns, capture='partial')])['findings'] == []


def test_session_of_unknown_capture_decides_nothing_whatever_the_user_says():
    turns = [user(PROD), edit('config/prod.yml'), user('Revert `config/prod.yml`.')]
    assert analyze([session('s', turns, capture='unknown')])['findings'] == []


def test_signal_on_an_instruction_without_a_checkable_operand_is_never_read():
    turns = [user('Always answer in formal English prose.'), agent('sure lol'), user('Revert that, it is not done.')]
    assert analyze([session('s', turns, capture='partial')])['findings'] == []


def test_reaction_to_a_harness_rule_is_not_read_as_a_signal():
    rules = '- Never run `git push --force`.\n'
    turns = [user('Tidy up.'), agent('Working.'), agent('Done.'), user('Please revert `git push --force`.')]
    assert analyze([session('s', turns, capture='partial')], rules, loading='loaded')['findings'] == []


# Claims stay weak, private and deterministic.

def test_calibrated_build_reports_a_weak_reaction_as_an_abstention_not_a_finding():
    raw = session('s', [user(PROD), agent('Working.'), agent('Done.'), user('Please revert `config/prod.yml`.')], capture='partial')
    report = adherence.build(adherence.load_sessions(dict(profile=adherence.SESSIONS_PROFILE, sessions=[raw])), [])
    assert (report['findings'], [(row['status'], row['mechanism'], row['abstention']) for row in report['abstentions']]) == (
        [], [('possible_violation', 'revert_requested', 'below_calibrated_threshold')])


def test_report_never_carries_the_reaction_text_or_the_operand():
    secret = 'zz-private-token'
    turns = [user(f'Do not edit `/srv/{secret}/prod.yml`.'), edit(f'/srv/{secret}/prod.yml'), user(f'Revert `/srv/{secret}/prod.yml`, it is not done.')]
    text = json.dumps(analyze([session(f'id-{secret}', turns)]))
    assert secret not in text and 'Revert' not in text


def test_signals_are_deterministic():
    turns = [user(PROD), edit('config/prod.yml'), user('Revert `config/prod.yml`.')]
    first, second = (json.dumps(analyze([session('s', turns)]), sort_keys=True) for _ in range(2))
    assert first == second and statuses(json.loads(first)) == ['observed_violation']


def test_recurrence_groups_weak_reactions_by_their_own_mechanism():
    turns = [user(PROD), agent('Working.'), agent('Done.'), user('Please revert `config/prod.yml`.')]
    report = analyze([session('a', turns, capture='partial', root='r1'), session('b', turns, capture='partial', root='r1')])
    assert [(row['mechanism'], row['sessions'], row['independent_roots'], row['occurrences']) for row in report['recurrence']] == [('revert_requested', 2, 1, 2)]
