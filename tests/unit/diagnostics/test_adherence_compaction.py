"""Recorded compactions in sessions, findings, native captures and the repair plan (part of #656); synthetic data only."""
import pytest

from agent_harness_lab.diagnostics import adherence_native as native
from agent_harness_lab.diagnostics import instruction_adherence as adherence
from agent_harness_lab.diagnostics import instruction_repair as repair
from tests.support.adherence_fixture import SOURCE, agent, session, user
from tests.support.native_session_fixture import claude, codex, write
from tests.support.repair_fixture import RULE, VIOLATION, first, harness, mechanisms, report_for, sessions

PADDING = [agent('Working.')] * 4
# user(0) agent(1-4) violation(5): positions 1..5 fall between the prompt and the violating call.
EARLY = [user('Publish the change.'), *PADDING, VIOLATION]
LATE = [user('Publish the change.'), *[agent('Working.')] * repair.LONG_SESSION_TURNS, VIOLATION]
# The rule is first seen at turn 8 / 203 (after the violation), and what came before is unknown: it may have been in view since launch.
AFTER_EARLY, AFTER_LATE = [*EARLY, *PADDING], [*LATE, *PADDING]
UNKNOWN_BEFORE = {SOURCE: dict(state='loaded', turn=8, before='unknown')}
UNKNOWN_BEFORE_LATE = {SOURCE: dict(state='loaded', turn=203, before='unknown')}
PROMPT_RULE = [user('Do not edit `config/prod.yml`.'), *PADDING, dict(role='tool_call', tool='Edit', path='config/prod.yml')]


def finding_of(turns, *, rules=RULE, loaded=None, **extra):
    raw = [session('s1', turns, loaded=loaded if loaded is not None else {SOURCE: 'loaded'}, root='r1', **extra)]
    report, _ = report_for(raw, rules)
    return report['findings'][0]


def plan_of(turns, *, rules=RULE, loaded=None, **extra):
    raw = [session('s1', turns, loaded=loaded if loaded is not None else {SOURCE: 'loaded'}, root='r1', **extra)]
    report, files = report_for(raw, rules)
    return repair.diagnose(report, files)


def context_loss(plan):
    return next((row for row in first(plan)['hypotheses'] if row['mechanism'] == 'context_loss'), None)


# --- the sessions file ---------------------------------------------------------------------------------------------

def test_a_session_that_records_its_compactions_keeps_the_positions_and_one_that_does_not_keeps_none():
    kept = sessions([session('a', EARLY, compactions=[2, 5]), session('b', EARLY)])
    assert [one['compactions'] for one in kept] == [[2, 5], None]


@pytest.mark.parametrize('value', [[], [0], [6]], ids=['empty record', 'before the first turn', 'after the last turn'])
def test_compaction_positions_from_the_first_to_the_last_boundary_are_accepted(value):
    assert sessions([session('a', EARLY, compactions=value)])[0]['compactions'] == value


@pytest.mark.parametrize('value', [[-1], [7], [3, 3], [4, 2], 'x', None, [True], [1.5], ['1']],
                         ids=['negative', 'past the end', 'repeated', 'descending', 'text', 'null', 'boolean', 'float', 'string item'])
def test_a_compaction_record_that_is_not_strictly_increasing_positions_inside_the_session_is_refused(value):
    with pytest.raises(ValueError, match='compactions'):
        sessions([session('a', EARLY, compactions=value)])


# --- findings: was a compaction recorded between the instruction and the violation --------------------------------

def test_a_finding_of_a_session_without_a_compaction_record_says_it_is_not_recorded():
    assert finding_of(EARLY)['compaction'] == 'not_recorded'


def test_a_finding_of_a_session_that_recorded_no_compaction_says_none_in_the_window():
    assert finding_of(EARLY, compactions=[])['compaction'] == 'none_in_window'


def test_a_compaction_between_the_prompt_and_the_violating_call_is_in_the_window():
    assert finding_of(EARLY, compactions=[3])['compaction'] == 'in_window'


def test_a_compaction_directly_before_the_violating_call_is_in_the_window():
    assert finding_of(EARLY, compactions=[5])['compaction'] == 'in_window'


def test_a_compaction_after_the_violating_call_is_not_in_the_window():
    assert finding_of(EARLY, compactions=[6])['compaction'] == 'none_in_window'


def test_a_compaction_before_a_rule_was_loaded_is_not_in_the_window():
    assert finding_of(EARLY, compactions=[2], loaded={SOURCE: dict(state='loaded', turn=3)})['compaction'] == 'none_in_window'


def test_a_compaction_at_the_turn_the_rule_was_loaded_is_not_in_the_window():
    assert finding_of(EARLY, compactions=[3], loaded={SOURCE: dict(state='loaded', turn=3)})['compaction'] == 'none_in_window'


def test_a_compaction_after_the_rule_was_loaded_is_in_the_window():
    assert finding_of(EARLY, compactions=[4], loaded={SOURCE: dict(state='loaded', turn=3)})['compaction'] == 'in_window'


@pytest.mark.parametrize('compactions, expected', [([3], 'in_window'), ([5], 'in_window'), ([7], 'none_in_window'), ([0], 'none_in_window'), ([], 'none_in_window'), (None, 'not_recorded')],
                         ids=['before the violation', 'directly before the violation', 'after the violation', 'before the first turn', 'none recorded', 'not recorded'])
def test_a_rule_whose_earlier_loading_is_unknown_is_in_view_from_launch_not_from_the_turn_it_was_recorded(compactions, expected):
    extra = {} if compactions is None else dict(compactions=compactions)
    found = finding_of(AFTER_EARLY, loaded=UNKNOWN_BEFORE, **extra)
    assert (found['status'], found['loading'], found['compaction']) == ('possible_violation', 'unknown', expected)


def test_a_compaction_before_the_load_turn_is_not_in_the_window_of_a_violation_after_it_even_when_the_earlier_loading_is_unknown():
    loaded = {SOURCE: dict(state='loaded', turn=2, before='unknown')}
    found = finding_of(EARLY, loaded=loaded, compactions=[1])
    assert (found['status'], found['loading'], found['compaction']) == ('observed_violation', 'loaded', 'none_in_window')


def test_a_rule_that_was_not_loaded_before_its_load_turn_keeps_the_window_at_the_load_turn():
    loaded = {SOURCE: dict(state='loaded', turn=8)}
    assert finding_of(AFTER_EARLY, loaded=loaded, compactions=[3])['compaction'] == 'none_in_window'


@pytest.mark.parametrize('position, expected', [(3, 'in_window'), (0, 'none_in_window'), (1, 'none_in_window')],
                         ids=['after the prompt', 'before the prompt', 'at the prompt'])
def test_a_user_instruction_is_in_view_from_its_own_turn(position, expected):
    turns = [agent('Ready.'), *PROMPT_RULE]
    assert finding_of(turns, rules='', compactions=[position])['compaction'] == expected


def test_recording_compactions_does_not_change_which_finding_it_is():
    bare, recorded = finding_of(EARLY), finding_of(EARLY, compactions=[3])
    assert (bare['finding_id'], bare['evidence_sha256']) == (recorded['finding_id'], recorded['evidence_sha256'])


# --- the repair plan: a recorded compaction is a fact, never a cause --------------------------------------------

def test_a_compaction_between_the_rule_and_an_early_violation_opens_context_loss_with_the_fact_apart():
    row = context_loss(plan_of(EARLY, compactions=[3]))
    assert (row['basis'], row['repair_class'], row['route'], row['status']) == ('not_excluded', 're_ground_after_compaction', 'state', 'hypothesis')
    assert row['support'] == ['compaction_in_window'] and 'agent_error_unrelated_to_harness' in row['competing']


def test_a_deep_session_with_a_compaction_in_the_window_records_both_facts():
    assert context_loss(plan_of(LATE, compactions=[100]))['support'] == ['late_in_session', 'compaction_in_window']


def test_a_deep_session_that_recorded_no_compaction_in_the_window_stays_open_and_says_so():
    assert context_loss(plan_of(LATE, compactions=[]))['support'] == ['late_in_session', 'no_compaction_in_window']


def test_a_deep_session_without_a_compaction_record_still_says_it_is_not_recorded():
    assert context_loss(plan_of(LATE))['support'] == ['late_in_session', 'compaction_not_recorded']


@pytest.mark.parametrize('compactions', [None, [], [6]], ids=['not recorded', 'none recorded', 'only after the violation'])
def test_an_early_violation_without_a_compaction_in_the_window_is_not_context_loss(compactions):
    extra = {} if compactions is None else dict(compactions=compactions)
    assert context_loss(plan_of(EARLY, **extra)) is None


def test_a_compaction_between_launch_and_an_early_violation_opens_context_loss_when_the_load_turn_comes_later_and_the_earlier_state_is_unknown():
    row = context_loss(plan_of(AFTER_EARLY, loaded=UNKNOWN_BEFORE, compactions=[3]))
    assert row['support'] == ['compaction_in_window']


def test_a_deep_session_with_a_compaction_before_the_violation_and_an_unknown_earlier_state_reports_the_compaction_in_the_window():
    assert context_loss(plan_of(AFTER_LATE, loaded=UNKNOWN_BEFORE_LATE, compactions=[100]))['support'] == ['late_in_session', 'compaction_in_window']


@pytest.mark.parametrize('compactions, support', [([], 'no_compaction_in_window'), (None, 'compaction_not_recorded')], ids=['none recorded', 'not recorded'])
def test_a_deep_session_with_an_unknown_earlier_state_and_no_compaction_to_report_says_which_of_the_two_it_is(compactions, support):
    extra = {} if compactions is None else dict(compactions=compactions)
    assert context_loss(plan_of(AFTER_LATE, loaded=UNKNOWN_BEFORE_LATE, **extra))['support'] == ['late_in_session', support]


def test_a_compaction_never_turns_a_rule_that_was_not_loaded_into_context_loss():
    raw = [session('s1', EARLY, loaded={SOURCE: 'not_loaded'}, root='r1', compactions=[3])]
    report, files = report_for(raw, RULE)
    assert mechanisms(first(repair.diagnose(report, files))) == ['not_in_context']


def test_an_ignored_user_prompt_with_a_compaction_in_the_window_gets_both_repairs_and_keeps_its_guards():
    candidate = first(plan_of(PROMPT_RULE, rules='', compactions=[3]))
    assert mechanisms(candidate) == ['context_loss', 'no_self_check_for_user_instruction']
    assert 'never_edit_the_users_prompt' in candidate['guards']


def test_a_report_from_before_the_field_existed_reads_as_not_recorded():
    report, files = report_for([session('s1', LATE, loaded={SOURCE: 'loaded'}, root='r1')], RULE)
    for row in report['findings']:
        del row['compaction']
    assert context_loss(repair.diagnose(report, files))['support'] == ['late_in_session', 'compaction_not_recorded']


@pytest.mark.parametrize('value', ['maybe', None, ['in_window'], True], ids=['unknown word', 'null', 'list', 'boolean'])
def test_a_report_with_an_unknown_compaction_value_is_refused(value):
    report, files = report_for([session('s1', EARLY, loaded={SOURCE: 'loaded'}, root='r1')], RULE)
    report['findings'][0]['compaction'] = value
    with pytest.raises(ValueError, match='schema'):
        repair.diagnose(report, files)


# --- native captures ---------------------------------------------------------------------------------------------

@pytest.fixture
def project(tmp_path):
    (tmp_path / 'project').mkdir()
    return tmp_path / 'project'


def read(tmp_path, project, rows, runtime='claude'):
    return native.read_session(write(tmp_path / 's.jsonl', rows), runtime, project, harness(RULE))


def test_claude_records_the_position_of_a_compaction_boundary_and_it_is_not_a_turn(project, tmp_path):
    rows = claude(project, [('user', 'Publish.'), ('agent', 'Working.'), ('compact',), ('summary',), ('call', 'Bash', dict(command='git push --force'))])
    captured = read(tmp_path, project, rows)
    assert captured['compactions'] == [2] and [turn['role'] for turn in captured['turns']] == ['user', 'agent', 'tool_call']


def test_claude_counts_the_boundary_and_its_summary_as_one_compaction(project, tmp_path):
    assert read(tmp_path, project, claude(project, [('user', 'a'), ('compact',), ('summary',), ('agent', 'b')]))['compactions'] == [1]


def test_claude_records_a_compaction_summary_that_has_no_boundary_row(project, tmp_path):
    assert read(tmp_path, project, claude(project, [('user', 'a'), ('summary',), ('agent', 'b')]))['compactions'] == [1]


def test_claude_records_two_compactions_in_order(project, tmp_path):
    rows = claude(project, [('user', 'a'), ('compact',), ('agent', 'b'), ('agent', 'c'), ('compact',)])
    assert read(tmp_path, project, rows)['compactions'] == [1, 3]


def test_a_native_capture_with_no_compaction_records_an_empty_list_not_a_missing_one(project, tmp_path):
    assert read(tmp_path, project, claude(project, [('user', 'a'), ('agent', 'b')]))['compactions'] == []


@pytest.mark.parametrize('change, reason', [(dict(sessionId='other'), 'mixed_sessions'), (dict(version='0.0.1'), 'unsupported_version'),
                                            (dict(isSidechain=True, agentId='sub'), 'subagent_transcript'), (dict(isSidechain='no'), 'malformed')],
                         ids=['another session', 'unverified release', 'subagent', 'writer type unknown'])
def test_a_compaction_boundary_row_is_checked_like_any_other_row_of_the_capture(project, tmp_path, change, reason):
    rows = claude(project, [('user', 'a'), ('compact',)])
    rows[1].update(change)
    with pytest.raises(native.Skipped) as error:
        read(tmp_path, project, rows)
    assert error.value.reason == reason


def test_a_system_row_that_is_not_a_compaction_boundary_is_ignored(project, tmp_path):
    rows = claude(project, [('user', 'a'), ('compact',), ('agent', 'b')])
    rows[1]['subtype'] = 'informational'
    assert read(tmp_path, project, rows)['compactions'] == []


def test_codex_counts_the_compacted_row_and_the_context_event_of_one_compaction_once(project, tmp_path):
    rows = codex(project, [('user', 'a'), ('compacted', None), ('compaction_event', None), ('agent', 'b')])
    assert read(tmp_path, project, rows, 'codex')['compactions'] == [1]


@pytest.mark.parametrize('step', ['compacted', 'compaction_event'])
def test_codex_records_either_compaction_record_alone(project, tmp_path, step):
    assert read(tmp_path, project, codex(project, [('user', 'a'), (step, None), ('agent', 'b')]), 'codex')['compactions'] == [1]


def test_a_native_capture_loads_as_a_session_and_an_ignored_rule_after_a_compaction_is_in_the_window(project, tmp_path):
    rows = claude(project, [('memory', str(project / 'AGENTS.md'), RULE), ('user', 'Publish.'), ('compact',), ('summary',),
                            ('call', 'Bash', dict(command='git push --force origin main'))])
    document = dict(profile=adherence.SESSIONS_PROFILE, sessions=[read(tmp_path, project, rows)])
    report = adherence.analyze(adherence.load_sessions(document), harness(RULE))
    assert [(row['status'], row['compaction']) for row in report['findings']] == [('observed_violation', 'in_window')]
