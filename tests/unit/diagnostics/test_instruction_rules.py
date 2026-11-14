"""Deterministic extraction and classification of instructions from harness text and user prompts."""
import pytest

from agent_harness_lab.diagnostics import instruction_rules as rules


def kinds(text, **options):
    return [(row['klass'], row['rule'] and row['rule']['kind']) for row in rules.extract(text, **options)]


@pytest.mark.parametrize('sentence,expected', [
    ('Never run `git push --force`.', ('machine_checkable', 'forbidden')),
    ("Don't use the tool `WebFetch`.", ('machine_checkable', 'forbidden')),
    ('You must not edit `config/prod.yml`.', ('machine_checkable', 'forbidden')),
    ('Do not modify src/app.py again.', ('machine_checkable', 'forbidden')),
    ('Always run `pytest`.', ('machine_checkable', 'required')),
    ('Always run `task lint` before running `git commit`.', ('machine_checkable', 'before')),
    ('Ask me first before running `rm -rf build`.', ('machine_checkable', 'gated')),
    ('Only edit `src/`.', ('machine_checkable', 'scope')),
    ('Never use `mcp__server__tool`.', ('machine_checkable', 'forbidden')),
    ('Always answer in formal prose.', ('reviewable_only', None)),
    ('Never edit generated files.', ('reviewable_only', None)),
    ('Never edit `dist/*`.', ('reviewable_only', None)),
    ('Never edit `../outside.txt`.', ('reviewable_only', None)),
    ('Never run `echo "unterminated`.', ('reviewable_only', None)),
    ('Prefer small commits.', ('unverifiable', None)),
    ('The build produces a wheel.', None)],
    ids=['command', 'tool', 'path-modal', 'bare-path', 'required', 'order', 'ask-first', 'scope', 'mcp-tool', 'no-operand',
         'no-path-operand', 'glob-path', 'parent-path', 'unparseable-command', 'soft-cue', 'no-cue'])
def test_sentence_is_classified_without_guessing_an_operand(sentence, expected):
    assert (kinds(sentence) or [None])[0] == expected


@pytest.mark.parametrize('text', ['', '\n\n', '- \n'], ids=['empty', 'blank-lines', 'empty-bullet'])
def test_text_without_sentences_yields_no_instructions(text):
    assert rules.extract(text) == []


def test_command_operand_keeps_leading_tokens_for_the_shell_tools():
    rule = rules.extract('Never run `git push --force`.')[0]['rule']
    assert rule['match'] == dict(tools=list(rules.SHELL_TOOLS), command=['git', 'push', '--force'])


def test_directory_operand_keeps_its_trailing_slash_and_file_operand_does_not():
    folder, file = (rules.extract(f'Do not edit `{path}`.')[0]['rule']['match']['path'] for path in ('./dist/', './src/app.py'))
    assert (folder, file) == ('dist/', 'src/app.py')


def test_stop_is_an_instruction_only_in_a_user_prompt():
    assert kinds('Stop.', user=True) == [('machine_checkable', 'halt')]
    assert kinds('Stop.') == []
    assert kinds('Stop touching the config and wait for my answer.', user=True) == []


def test_each_sentence_of_a_line_is_classified_on_its_own():
    assert kinds('Never run `rm -rf /`. Prefer small commits. Always run `pytest`.') == [
        ('machine_checkable', 'forbidden'), ('unverifiable', None), ('machine_checkable', 'required')]


def test_harness_text_over_the_instruction_bound_fails_closed():
    with pytest.raises(ValueError, match='bound'):
        rules.extract('Never edit generated files.\n' * (rules.MAX_RULES + 1))


def test_user_prompt_over_the_instruction_bound_is_read_up_to_the_bound():
    assert len(rules.extract('Never edit generated files.\n' * (rules.MAX_RULES + 1), user=True)) == rules.MAX_RULES


def test_subjects_name_the_operands_in_lowercase_for_sentence_matching():
    rule = rules.extract('Always run `Task Lint` before running `git commit`.')[0]['rule']
    assert rules.subjects(rule) == ['task lint', 'git commit']
