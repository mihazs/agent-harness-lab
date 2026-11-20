"""User reactions located by the agent's last tool calls; synthetic sessions only."""
import json

import pytest

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


@pytest.mark.parametrize('text,expected', [
    ('look at /srv/app/secret-dir/a.py now', 'look at <path> now'),
    ('look at ~/work/notes.md now', 'look at <path> now'),
    ('see https://example.invalid/a?b=1 first', 'see <url> first'),
    ('mail someone@example.invalid about it', 'mail <email> about it'),
    ('the hash 0123456789abcdef0123456789abcdef is wrong', 'the hash <secret> is wrong'),
    ('key sk-abcdefgh12345678 leaked', 'key <secret> leaked'),
    ('token ghp_abcdefgh1234 and gho_abcdefgh1234', 'token <secret> and <secret>'),
    ('aws AKIAABCDEFGHIJKLMNOP here', 'aws <secret> here'),
    ('pem -----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY----- done', 'pem <secret> done'),
    ('ticket 1234567 not 12345', 'ticket <number> not 12345'),
    ('too   many\n\tspaces', 'too many spaces'),
    ('keep src/app/a.py and test_a_long_identifier_name', 'keep src/app/a.py and test_a_long_identifier_name'),
], ids=['absolute path', 'home path', 'url', 'email', 'hex run', 'sk key', 'github tokens', 'aws key', 'pem block', 'digits', 'whitespace', 'kept'])
def test_scrub_replaces_each_private_shape_with_its_placeholder(text, expected):
    assert user_reactions.scrub(text) == expected


def test_scrub_handles_a_combined_message_cuts_it_to_the_bound_and_drops_one_with_nothing_but_placeholders():
    text = 'No, revert /srv/x/a.py; see https://example.invalid/x, mail a@example.invalid, key ghp_abcdefgh1234, id 123456789. ' + 'word ' * 100
    excerpt = user_reactions.scrub(text)
    assert excerpt.startswith('No, revert <path> see <url> mail <email>, key <secret>, id <number>. word') and len(excerpt) == user_reactions.MAX_EXCERPT
    assert user_reactions.scrub(' /srv/a/b https://example.invalid 1234567 ') is None and user_reactions.scrub('  ') is None


def test_corrections_quote_only_corrections_scrubbed_with_at_most_two_per_session_and_twelve_in_all():
    correcting = [step for n in range(3) for step in (('call', 'Bash', dict(command='pytest -q')), ('user', f'stop {n} at /srv/{CANARY}/f.py'))]
    document = user_reactions.corrections([session(('user', 'start'), *correcting) for _ in range(8)])
    assert (document['profile'], document['authority'], len(document['rows'])) == ('ahl-user-corrections-v1', 'untrusted_evidence_only', 12)
    assert document['rows'][:3] == [
        dict(correction_id='c1', reaction='steering', after_tool='Bash', command_family='pytest', file_extensions=[], excerpt='stop 0 at <path>'),
        dict(correction_id='c2', reaction='steering', after_tool='Bash', command_family='pytest', file_extensions=[], excerpt='stop 1 at <path>'),
        dict(correction_id='c3', reaction='steering', after_tool='Bash', command_family='pytest', file_extensions=[], excerpt='stop 0 at <path>')]
    assert CANARY not in json.dumps(document)


@pytest.mark.parametrize('text,kept', [
    ('thanks, now the docs', False), ('That is wrong.', True), ('Isso está errado, faça de novo.', True), ('Eu disse para usar o outro.', True),
    ('Não faça isso.', True), ('Use that instead.', True), ('<command-name>/clear</command-name> again', False),
    ('[Request interrupted by user] again', False), ('/srv/a/b', False)],
    ids=['plain followup', 'english', 'portuguese', 'portuguese said', 'portuguese negative', 'instead', 'runtime wrapper', 'interrupt marker', 'only a path'])
def test_a_followup_after_the_agent_stopped_is_quoted_only_when_it_reads_as_the_users_own_correction(text, kept):
    rows = user_reactions.corrections([session(('user', 'start'), ('agent', 'Done.'), ('user', text))])['rows']
    assert len(rows) == kept


def test_sessions_of_an_unverified_version_are_read_only_with_the_excerpt_flag_and_subagents_never(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    steps = [('user', 'start'), ('call', 'Bash', dict(command='pytest -q')), ('user', f'no, not {CANARY}')]
    listed = [dict(format='claude', path=str(write(tmp_path / f'{name}.jsonl', claude(project, steps, session=name, **fields))))
              for name, fields in (('pinned', {}), ('other', dict(version='9.9.9')), ('sub', dict(version='9.9.9', sidechain=True)))]
    section, _ = native.for_analysis(analysis(project), dict(sources=listed))
    assert section['sessions']['read'] == 1 and section['sessions']['skipped'] == dict(unsupported_version=2)
    assert 'user_corrections' not in section and 'excerpts' not in section['user_reactions'] and CANARY not in json.dumps(section)
    section, _ = native.for_analysis(analysis(project, correction_excerpts=True), dict(sources=listed))
    assert section['sessions'] == dict(read=2, unknown_lineage=2, skipped=dict(subagent_transcript=1), unverified_version=1)
    assert (section['user_reactions']['excerpts'], section['user_reactions']['unverified_version_sessions']) == (2, 1)
    assert [row['excerpt'] for row in section['user_corrections']['rows']] == [f'no, not {CANARY}'] * 2
    assert CANARY not in json.dumps({name: value for name, value in section.items() if name != 'user_corrections'})
