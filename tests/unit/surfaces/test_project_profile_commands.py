"""The shell reader shared by harness text and session history normalizes lines the same way (#1113); synthetic lines only."""
import pytest

from agent_harness_lab.project_profile import commands


def programs(line):
    return [row['program'] for row in commands.simple_commands(line)]


@pytest.mark.parametrize('line, expected', [
    pytest.param('pytest -q tests/a.py', ['pytest'], id='plain'),
    pytest.param('cd sub && pytest -q', ['pytest'], id='cd-then-command'),
    pytest.param('FOO=1 BAR=2 make test', ['make'], id='env-prefix'),
    pytest.param('sudo -E time ./run.sh --all', ['run.sh'], id='wrappers-and-flags'),
    pytest.param('timeout 30 npm test', ['npm'], id='timeout-duration'),
    pytest.param('a | b && c; d || e', ['a', 'b', 'c', 'd', 'e'], id='separators'),
    pytest.param('echo "a && b"', ['echo'], id='quoted-separator-stays-in-one-command'),
    pytest.param('make test > out.log 2>&1', ['make'], id='redirect-dropped'),
    pytest.param('unterminated "quote', [], id='unparsable-line-gives-nothing'),
    pytest.param('', [], id='empty'),
])
def test_lines_split_into_programs(line, expected):
    assert programs(line) == expected


def test_cd_marks_the_commands_after_it():
    found = commands.simple_commands('cd web && npm run dev; npm test')
    assert [(row['program'], row['cd']) for row in found] == [('npm', True), ('npm', True)]


@pytest.mark.parametrize('line, expected', [
    pytest.param('npm run build', ('npm', 'build'), id='npm-run'),
    pytest.param('npm test', ('npm', 'test'), id='npm-test-alias'),
    pytest.param('npm t', ('npm', 'test'), id='npm-t-alias'),
    pytest.param('pnpm test', ('pnpm', 'test'), id='pnpm-conventional-bare-script'),
    pytest.param('pnpm run lint:fix', ('pnpm', 'lint:fix'), id='script-with-colon'),
    pytest.param('make check', ('make', 'check'), id='make-target'),
    pytest.param('task setup:test', ('task', 'setup:test'), id='task-target'),
    pytest.param('just ci', ('just', 'ci'), id='just-recipe'),
    pytest.param('pnpm install', None, id='builtin-is-not-a-script'),
    pytest.param('pnpm --filter web test', None, id='option-before-the-name'),
    pytest.param('make -j4 all', None, id='make-option'),
    pytest.param('npm run', None, id='no-name'),
    pytest.param('cd web && npm run dev', None, id='after-cd-is-another-package'),
    pytest.param('yarn', None, id='bare-manager'),
])
def test_a_declared_script_or_target_is_named_only_when_the_line_is_unambiguous(line, expected):
    assert commands.runner_target(commands.simple_commands(line)[0]) == expected


@pytest.mark.parametrize('line, program', [
    pytest.param('uv run pytest -q', 'pytest', id='uv-run'),
    pytest.param('npx vitest run', 'vitest', id='npx'),
    pytest.param('python -m pytest', 'pytest', id='python-dash-m'),
    pytest.param('pnpm exec eslint .', 'eslint', id='pnpm-exec'),
    pytest.param('poetry run mypy src', 'mypy', id='poetry-run'),
    pytest.param('pytest', 'pytest', id='no-wrapper'),
])
def test_run_wrappers_expose_the_tool_they_start(line, program):
    assert commands.unwrapped(commands.simple_commands(line)[0])['program'] == program


@pytest.mark.parametrize('name, text, expected', [
    pytest.param('test', '', 'test', id='name'),
    pytest.param('test:unit', '', 'test', id='name-with-suffix'),
    pytest.param('check', 'tsc --noEmit', 'typecheck', id='text-when-name-is-silent'),
    pytest.param('lint', 'eslint .', 'lint', id='lint'),
    pytest.param('fmt', '', 'format', id='format-abbreviation'),
    pytest.param('typecheck', '', 'typecheck', id='typecheck-beats-test-words'),
    pytest.param('release', '', None, id='unknown'),
    pytest.param('latest', '', None, id='substring-is-not-a-word'),
])
def test_category_comes_from_the_name_words_then_the_tool(name, text, expected):
    assert commands.category(name, text) == expected


def test_setup_and_install_names_are_not_verification():
    assert commands.category('setup:test') is None and commands.category('install') is None
