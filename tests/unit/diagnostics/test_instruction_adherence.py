"""Ignored harness rules and ignored user-prompt instructions, judged on synthetic sessions only."""
import json

import pytest

from agent_harness_lab.diagnostics import instruction_adherence as adherence
from tests.support.adherence_fixture import SOURCE, agent, analyze, call, edit, session, shell, statuses, user

NEVER_PUSH = '- Never run `git push --force`.\n'


def only(report):
    assert len(report['findings']) == 1, report['findings']
    return report['findings'][0]


# The eight planted cases of the acceptance criteria.

def test_loaded_rule_violated_by_a_tool_call_is_an_observed_violation_with_its_span_and_layer():
    report = analyze([session('s', [user('Tidy up.'), agent('Working.'), shell('cd work && git push --force origin main')])], NEVER_PUSH, loading='loaded')
    finding = only(report)
    assert (finding['status'], finding['direction'], finding['loading'], finding['source_layer']) == ('observed_violation', 'harness', 'loaded', 'project')
    assert (finding['mechanism'], finding['span']) == ('forbidden_action_taken', dict(first_turn=2, last_turn=2))


def test_rule_that_was_never_loaded_is_reported_as_not_loaded_not_as_ignored():
    report = analyze([session('s', [user('Tidy up.'), shell('git push --force')])], NEVER_PUSH, loading='not_loaded')
    assert statuses(report) == ['not_loaded']
    assert only(report)['loading'] == 'not_loaded'


def test_user_prohibition_that_the_agent_broke_is_an_observed_violation_of_the_prompt():
    report = analyze([session('s', [user('Do not edit `config/prod.yml`.'), agent('Sure.'), edit('config/prod.yml')])])
    finding = only(report)
    assert (finding['status'], finding['direction'], finding['source_layer'], finding['mechanism']) == (
        'observed_violation', 'user_prompt', 'prompt', 'forbidden_action_taken')


def test_user_instruction_repeated_after_being_ignored_carries_the_restatement_as_corroboration():
    turns = [user('Do not edit `config/prod.yml`.'), edit('config/prod.yml'), agent('Done.'), user('I told you not to edit `config/prod.yml`, never touch `config/prod.yml`.')]
    finding = only(analyze([session('s', turns)]))
    assert (finding['status'], finding['corroboration']) == ('observed_violation', ['user_restated'])


def test_legitimate_user_supersession_is_no_finding():
    turns = [user('Do not edit `a.py`.'), agent('Understood.'), user('Actually, go ahead and edit `a.py`.'), edit('a.py')]
    report = analyze([session('s', turns)])
    assert report['findings'] == [] and report['abstentions'] == []
    assert report['decisions'] == dict(satisfied=1)


def test_violation_before_a_supersession_still_counts():
    turns = [user('Do not edit `a.py`.'), edit('a.py'), user('Actually, go ahead and edit `a.py`.'), edit('a.py')]
    assert only(analyze([session('s', turns)]))['span'] == dict(first_turn=1, last_turn=1)


def test_agent_clarification_before_a_gated_action_is_no_finding():
    turns = [user('Ask before running `rm -rf build`.'), agent('May I run rm -rf build?'), user('Yes.'), shell('rm -rf build')]
    assert analyze([session('s', turns)])['findings'] == []


def test_repeating_an_instruction_as_the_answer_to_a_clarifying_question_is_not_a_restatement():
    turns = [user('Never touch `config/prod.yml` in this task.'), agent('Do you also mean config/prod.yml.bak?'), user('No, never touch `config/prod.yml` itself.')]
    assert analyze([session('s', turns)])['findings'] == []


def test_two_conflicting_instructions_are_reported_as_a_conflict_and_not_as_a_violation():
    turns = [user('Do not run `task test`.'), agent('Understood.'), shell('task test')]
    report = analyze([session('s', turns)], '- Always run `task test`.\n', loading='loaded')
    finding = only(report)
    assert (finding['status'], finding['direction'], finding['source_layer'], len(finding['instruction_refs'])) == ('conflict', 'mixed', 'mixed', 2)


def test_unknown_capture_session_is_reported_unknown_and_decides_nothing():
    report = analyze([session('s', [user('Tidy up.'), shell('git push --force')], capture='unknown')], NEVER_PUSH, loading='loaded')
    assert report['findings'] == []
    assert (report['sessions']['unknown_capture'], report['decisions']) == (1, dict(undecided_unknown_capture=1))


def test_absent_capture_state_is_unknown_never_complete():
    raw = dict(session='s', turns=[shell('git push --force')])
    assert adherence.load_sessions(dict(profile=adherence.SESSIONS_PROFILE, sessions=[raw]))[0]['capture'] == 'unknown'


# Loading state: a rule is ignored only when it was demonstrably in context at that time.

@pytest.mark.parametrize('loading,status', [('truncated', 'possible_violation'), ('unknown', 'possible_violation'), (None, 'possible_violation')],
                         ids=['truncated', 'declared-unknown', 'undeclared'])
def test_violated_rule_with_uncertain_loading_is_only_a_possible_violation(loading, status):
    finding = only(analyze([session('s', [shell('git push --force')])], NEVER_PUSH, loading=loading))
    assert finding['status'] == status and finding['loading'] in ('truncated', 'unknown')


def test_rule_loaded_after_the_violating_call_was_not_in_context_at_that_time():
    loaded = {SOURCE: dict(state='loaded', turn=3)}
    report = analyze([session('s', [shell('git push --force'), agent('x'), agent('y'), agent('z')], loaded=loaded)], NEVER_PUSH)
    assert statuses(report) == ['not_loaded']


def test_call_made_after_the_rule_was_loaded_counts_and_the_earlier_one_does_not():
    loaded = {SOURCE: dict(state='loaded', turn=3)}
    report = analyze([session('s', [shell('git push --force'), agent('x'), agent('y'), shell('git push --force')], loaded=loaded)], NEVER_PUSH)
    finding = only(report)
    assert (finding['status'], finding['turns']) == ('observed_violation', [3])


# Three-valued evidence: what is not captured never becomes a pass or a failure.

def test_partial_capture_keeps_an_observed_prohibited_call_but_not_its_absence():
    seen = analyze([session('a', [shell('git push --force')], capture='partial')], NEVER_PUSH, loading='loaded')
    unseen = analyze([session('b', [shell('ls')], capture='partial')], NEVER_PUSH, loading='loaded')
    assert statuses(seen) == ['observed_violation']
    assert unseen['findings'] == [] and unseen['decisions'] == dict(undecided_capture_partial=1)


def test_skipped_required_step_on_a_complete_capture_is_an_observed_violation():
    report = analyze([session('s', [user('Fix it.'), edit('src/a.py')])], '- Always run `pytest`.\n', loading='loaded')
    assert (statuses(report), only(report)['mechanism']) == (['observed_violation'], 'required_step_skipped')


def test_skipped_required_step_on_a_partial_capture_stays_unknown():
    report = analyze([session('s', [user('Fix it.'), edit('src/a.py')], capture='partial')], '- Always run `pytest`.\n', loading='loaded')
    assert report['findings'] == [] and report['decisions'] == dict(undecided_capture_partial=1)


def test_required_step_in_a_later_compound_command_is_satisfied():
    report = analyze([session('s', [user('Fix it.'), edit('src/a.py'), shell('cd app && pytest -q')])], '- Always run `pytest`.\n', loading='loaded')
    assert report['findings'] == [] and report['decisions'] == dict(satisfied=1)


def test_shell_call_without_command_text_is_undecidable_not_a_pass():
    report = analyze([session('s', [call('Bash')])], NEVER_PUSH, loading='loaded')
    assert report['findings'] == [] and report['decisions'] == dict(undecided_undecidable_call=1)


def test_unparseable_shell_line_is_undecidable_not_a_pass():
    report = analyze([session('s', [shell('echo "unterminated')])], NEVER_PUSH, loading='loaded')
    assert report['decisions'] == dict(undecided_undecidable_call=1)


@pytest.mark.parametrize('command', ['sudo git push --force', 'GIT_TRACE=1 git push --force origin', 'git add . && git push --force', 'make | git push --force'],
                         ids=['wrapper', 'env-prefix', 'and-list', 'pipe'])
def test_prohibited_command_is_found_through_wrappers_and_command_lists(command):
    assert statuses(analyze([session('s', [shell(command)])], NEVER_PUSH, loading='loaded')) == ['observed_violation']


@pytest.mark.parametrize('command', ['echo git push --force', 'git push origin main', 'git pushx --force'], ids=['argument', 'other-flags', 'prefix-lookalike'])
def test_commands_that_only_resemble_the_prohibited_one_are_not_findings(command):
    assert analyze([session('s', [shell(command)])], NEVER_PUSH, loading='loaded')['findings'] == []


# Paths.

@pytest.mark.parametrize('path,hit', [('dist/a.js', True), ('dist', True), ('dist-copy/a.js', False), ('src/dist/a.js', False)],
                         ids=['inside', 'directory-itself', 'sibling-prefix', 'nested-name'])
def test_directory_prohibition_covers_only_that_directory(path, hit):
    report = analyze([session('s', [user('Do not edit `dist/`.'), agent('ok'), edit(path)])])
    assert bool(report['findings']) is hit


def test_absolute_paths_resolve_against_the_declared_project_directory():
    inside = analyze([session('s', [user('Do not edit `src/app.py`.'), edit('/work/p/src/app.py')], project_dir='/work/p')])
    outside = analyze([session('t', [user('Do not edit `src/app.py`.'), edit('/other/src/app.py')], project_dir='/work/p')])
    assert statuses(inside) == ['observed_violation'] and outside['findings'] == []


def test_absolute_path_without_a_project_directory_is_undecidable():
    report = analyze([session('s', [user('Do not edit `src/app.py`.'), edit('/work/p/src/app.py')])])
    assert report['findings'] == [] and report['decisions'] == dict(undecided_undecidable_call=1)


def test_edit_outside_the_only_allowed_directory_is_a_scope_violation():
    turns = [user('Only edit `src/`.'), edit('src/a.py'), edit('docs/a.md')]
    finding = only(analyze([session('s', turns)]))
    assert (finding['mechanism'], finding['span']) == ('scope_exceeded', dict(first_turn=2, last_turn=2))


# Ordering, approval and stop.

def test_commit_before_the_lint_step_is_an_order_violation_and_the_ordered_run_is_not():
    rule = '- Always run `task lint` before running `git commit`.\n'
    bad = analyze([session('a', [shell('git commit -m x'), shell('task lint')])], rule, loading='loaded')
    good = analyze([session('b', [shell('task lint'), shell('git commit -m x')])], rule, loading='loaded')
    assert [f['mechanism'] for f in bad['findings']] == ['order_violated'] and good['findings'] == []


def test_gated_command_without_a_question_and_reply_is_an_approval_violation():
    turns = [user('Ask before running `rm -rf build`.'), agent('Cleaning.'), shell('rm -rf build')]
    assert only(analyze([session('s', turns)]))['mechanism'] == 'approval_not_sought'


def test_tool_call_after_stop_is_a_violation_and_a_quiet_agent_is_not():
    start = [user('Refactor the parser.'), agent('Starting.'), user('Stop.')]
    assert only(analyze([session('a', [*start, edit('p.py')])]))['mechanism'] == 'stop_not_honored'
    assert analyze([session('b', [*start, agent('Stopped.')])])['findings'] == []


def test_restated_instruction_without_checkable_evidence_is_only_a_possible_violation():
    said = 'Always answer in formal English prose.'
    finding = only(analyze([session('s', [user(said), agent('sure lol'), user(said), agent('ok')])]))
    assert (finding['status'], finding['mechanism']) == ('possible_violation', 'instruction_restated')


def test_restated_instruction_that_the_agent_obeyed_is_no_finding():
    turns = [user('Do not edit `a.py`.'), edit('b.py'), user('I said do not edit `a.py`.'), edit('b.py')]
    assert analyze([session('s', turns)])['findings'] == []


# Counting, privacy and determinism.

def test_repeated_violations_in_one_session_are_one_finding_with_its_occurrences():
    finding = only(analyze([session('s', [shell('git push --force'), shell('git push --force a'), shell('git push --force b')])], NEVER_PUSH, loading='loaded'))
    assert (finding['occurrences'], finding['turns']) == (3, [0, 1, 2])


def test_recurrence_counts_roots_not_sessions_and_keeps_unknown_lineage_apart():
    violating = [shell('git push --force')]
    report = analyze([session('a', violating, root='r1'), session('b', violating, root='r1'), session('c', violating, root='r2'),
                      session('d', violating), session('e', violating)], NEVER_PUSH, loading='loaded')
    row = report['recurrence'][0]
    assert (row['sessions'], row['independent_roots'], row['unknown_lineage_sessions']) == (5, 2, 2)
    assert (report['sessions']['declared_roots'], report['sessions']['unknown_lineage']) == (2, 2)


def test_report_never_carries_prompt_text_commands_paths_or_session_identifiers():
    secret = 'zz-private-token'
    turns = [user(f'Do not edit `/home/{secret}/notes.md` and never run `curl {secret}`.'), edit(f'/home/{secret}/notes.md'), shell(f'curl {secret}')]
    text = json.dumps(analyze([session(f'id-{secret}', turns, root=f'root-{secret}', project_dir='/home/' + secret)], NEVER_PUSH + f'- Never run `curl {secret}`.\n', loading='loaded'))
    assert secret not in text and 'Do not edit' not in text


def test_private_detail_adds_the_instruction_sentence_and_nothing_else_by_default():
    turns = [user('Do not edit `a.py`.'), edit('a.py')]
    plain, detailed = analyze([session('s', turns)]), analyze([session('s', turns)], detail=True)
    assert 'detail' not in only(plain) and only(detailed)['detail'] == dict(instruction='Do not edit `a.py`.')
    assert (plain['redaction'], detailed['redaction'], detailed['publication_allowed']) == ('digest_only', 'private_detail', False)


def test_report_is_deterministic_and_claims_no_cause():
    turns = [user('Do not edit `a.py`.'), edit('a.py')]
    first, second = (json.dumps(analyze([session('s', turns)]), sort_keys=True) for _ in range(2))
    assert first == second
    report = json.loads(first)
    assert (report['causal_diagnosis'], report['authority'], report['promotion_authority']) == (False, 'descriptive_only', False)


def test_calibrated_build_moves_weak_signals_to_abstentions_and_keeps_observed_findings():
    said = 'Always answer in formal English prose.'
    sessions = adherence.load_sessions(dict(profile=adherence.SESSIONS_PROFILE, sessions=[
        session('a', [user('Do not edit `a.py`.'), edit('a.py')]), session('b', [user(said), agent('sure lol'), user(said), agent('ok')])]))
    report = adherence.build(sessions, [])
    assert statuses(report) == ['observed_violation']
    assert [(row['status'], row['abstention']) for row in report['abstentions']] == [('possible_violation', 'below_calibrated_threshold')]
    assert report['calibration']['admitted'] == ['observed_violation']


# Fail-closed input.

@pytest.mark.parametrize('raw', [
    dict(session='s'), dict(session='s', turns=[], extra=1), dict(session='s', turns=[dict(role='robot', text='x')]),
    dict(session='s', turns=[dict(role='user')]), dict(session='s', turns=[dict(role='tool_call')]),
    dict(session='s', turns=[], capture='mostly'), dict(session='s', turns=[], project_dir='relative'),
    dict(session='s', turns=[], loaded={'a': 'maybe'}), dict(session='s', turns=[], loaded={'a': dict(state='loaded', turn=-1)}),
    dict(session='', turns=[]), dict(session='s', turns=[shell('x' * (adherence.MAX_TEXT + 1))])],
    ids=['no-turns', 'unknown-key', 'bad-role', 'user-without-text', 'call-without-tool', 'bad-capture', 'relative-project',
         'bad-loading-state', 'negative-loading-turn', 'empty-id', 'oversized-text'])
def test_invalid_session_is_refused(raw):
    with pytest.raises(ValueError):
        adherence.load_sessions(dict(profile=adherence.SESSIONS_PROFILE, sessions=[raw]))


@pytest.mark.parametrize('document', [{}, dict(profile='other', sessions=[]), dict(profile=adherence.SESSIONS_PROFILE), dict(profile=adherence.SESSIONS_PROFILE, sessions={}),
                                      dict(profile=adherence.SESSIONS_PROFILE, sessions=[], extra=1)],
                         ids=['empty', 'wrong-profile', 'no-sessions', 'sessions-not-a-list', 'extra-key'])
def test_invalid_sessions_document_is_refused(document):
    with pytest.raises(ValueError):
        adherence.load_sessions(document)


def test_duplicate_session_identifiers_are_refused():
    with pytest.raises(ValueError, match='distinct'):
        adherence.load_sessions(dict(profile=adherence.SESSIONS_PROFILE, sessions=[session('s', []), session('s', [])]))
