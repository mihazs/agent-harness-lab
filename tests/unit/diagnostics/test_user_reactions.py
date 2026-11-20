"""User reactions located by the agent's last tool calls; synthetic sessions only."""
import json

from agent_harness_lab.diagnostics import adherence_native as native, user_reactions
from tests.support.native_session_fixture import claude, write
from tests.unit.diagnostics.test_adherence_native import analysis

CANARY = 'zz-canary-private'


def session(*turns):
    return dict(turns=[dict(role='tool_call', tool=turn[1], **turn[2]) if turn[0] == 'call' else dict(role=turn[0], text=turn[1], **dict([turn[2:]] if turn[2:] else []))
                       for turn in turns])


def row(reaction, after_tool, command_family, file_extensions, sessions, occurrences):
    return dict(reaction=reaction, after_tool=after_tool, command_family=command_family, file_extensions=file_extensions,
                sessions=sessions, occurrences=occurrences)


def test_each_user_turn_after_agent_activity_is_classified_and_located_by_the_calls_before_it():
    steered = (('user', f'start {CANARY}'), ('call', 'Bash', dict(command='pytest -q')), ('call', 'Edit', dict(path=f'/{CANARY}/a.py')),
               ('user', f'use the other module {CANARY}'))
    section = user_reactions.summarize([
        session(*steered, ('agent', 'Done.'), ('user', f'thanks, now the docs {CANARY}')),
        session(*steered, ('call', 'mcp__private__tool', dict(command=f'{CANARY} --flag', path=f'/{CANARY}/notes.{CANARY}')), ('user', 'Please revert that.'),
                ('call', 'Read', dict(path='/p/b.md')), ('agent', 'All done.'), ('user', 'It is still broken.'),
                ('agent', 'Fixed now.'), ('user', 'As I said, keep it small.'))])
    assert section['rows'] == [
        row('steering', 'Edit', 'pytest', ['py'], 2, 2),
        row('followup_after_stop', 'none', 'none', [], 1, 1),
        row('instruction_restated', 'none', 'none', [], 1, 1),
        row('revert_requested', 'other', 'other', ['other'], 1, 1),
        row('task_reopened', 'Read', 'none', ['md'], 1, 1)]
    assert (section['status'], section['authority'], section['sessions_read'], section['rows_total']) == ('completed', 'descriptive_only', 2, 5)
    assert CANARY not in json.dumps(section) and 'mcp__' not in json.dumps(section)


def test_a_first_prompt_a_second_user_turn_in_a_row_and_an_answer_to_a_question_are_not_reactions():
    section = user_reactions.summarize([session(
        ('user', 'start'), ('user', 'and revert the other thing'), ('agent', 'Which file?'), ('user', 'undo it in a.py'),
        ('call', 'AskUserQuestion', {}), ('user', 'the first'), ('call', 'Bash', dict(command='ls')), ('user', 'revert', 'reply_to_question', True))])
    assert section['rows'] == [] and section['status'] == 'completed' and 'not that users never intervened' in section['meaning']


def test_only_the_top_rows_are_listed_and_the_total_says_how_many_there_were():
    tools = ('Bash', 'Read', 'Write', 'Edit', 'MultiEdit', 'Glob', 'Grep', 'Agent', 'Task', 'Skill')
    turns = [('user', 'start')] + [step for tool in tools for step in (('call', tool, {}), ('user', 'next'))]
    section = user_reactions.summarize([session(*turns)])
    assert (len(section['rows']), section['rows_total']) == (user_reactions.TOP_ROWS, len(tools))


def test_analysis_section_carries_the_reactions_and_says_not_run_when_no_session_was_read(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    path = write(tmp_path / 's.jsonl', claude(project, [('user', CANARY), ('call', 'Bash', dict(command='git diff')), ('user', f'no, {CANARY}')]))
    section, _ = native.for_analysis(analysis(project), dict(sources=[dict(format='claude', path=str(path))]))
    assert section['user_reactions']['rows'] == [row('steering', 'Bash', 'git-diff', [], 1, 1)] and section['user_reactions']['sessions_read'] == 1
    assert CANARY not in json.dumps(section['user_reactions'])
    for data, reason in ((dict(sources=[]), 'no_native_capture_path'),
                         (dict(sources=[dict(format='claude', path=str(tmp_path / 'gone.jsonl'))]), 'no_readable_native_session')):
        reactions = native.for_analysis(analysis(project), data)[0]['user_reactions']
        assert (reactions['status'], reactions['reason'], 'rows' in reactions) == ('not_run', reason, False)
