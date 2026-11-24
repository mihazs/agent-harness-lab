"""History findings count separate sessions against the harness and stay silent on thin or unknown capture (#1118)."""
import pytest

from agent_harness_lab.project_profile import history, report
from tests.support.profile_fixture import build, package
from tests.unit.surfaces.test_project_profile_session_commands import session

NODE = {'package.json': package({'test': 'vitest run'}), 'pnpm-lock.yaml': ''}


def run(tmp_path, files, sessions):
    return report.build(build(tmp_path, files), sessions)


def kinds(result):
    return sorted((row['kind'], row['subject']) for row in result['findings'])


def row(result, kind):
    return next(item for item in result['findings'] if item['kind'] == kind)


def times(count, *commands, prefix='s'):
    return [session(f'{prefix}{number}', *commands) for number in range(count)]


def test_a_category_run_in_three_sessions_and_never_named_is_a_gap(tmp_path):
    result = run(tmp_path, {'Makefile': 'build:\n\ttrue\n', 'AGENTS.md': 'Keep it small.\n'}, times(3, 'pytest -q'))
    found = row(result, 'command_in_use_unrecorded')
    assert (found['subject'], found['finding_class'], found['evidence']) == ('test', 'gap', dict(category='test', sessions=3, uses=3, lineage='unknown'))
    assert found['proposed_update'] == dict(action='record_command', category='test', candidates=['pytest'])


def test_two_sessions_never_make_a_finding_however_many_uses(tmp_path):
    result = run(tmp_path, {'AGENTS.md': 'Keep it small.\n'}, [session('a', *(['pytest'] * 40)), session('b', *(['pytest'] * 40))])
    assert not any(kind == 'command_in_use_unrecorded' for kind, _ in kinds(result))


@pytest.mark.parametrize('text', [pytest.param('Run `pytest -q` before finishing.', id='named-by-command'), pytest.param('Tests: use pytest.', id='named-by-tool-in-prose'),
                                  pytest.param('Run `make check`.', id='meta-target')])
def test_a_category_the_harness_names_is_not_a_gap(tmp_path, text):
    files = {'Makefile': 'check:\n\ttrue\n', 'AGENTS.md': text + '\n'}
    assert not any(kind == 'command_in_use_unrecorded' for kind, _ in kinds(run(tmp_path, files, times(4, 'pytest'))))


def test_a_declared_unnamed_command_used_in_sessions_is_one_finding_with_the_sessions_as_evidence(tmp_path):
    files = {**NODE, 'AGENTS.md': 'Use pnpm.\n'}
    result = run(tmp_path, files, times(3, 'pnpm test'))
    assert kinds(result) == [('verification_command_unnamed', 'test')]
    assert row(result, 'verification_command_unnamed')['evidence']['observed_in_sessions'] == dict(sessions=3, uses=3, lineage='unknown')


def test_a_declared_command_is_named_by_its_runner_and_target_whichever_node_manager_the_harness_writes(tmp_path):
    files = {**NODE, 'AGENTS.md': 'Use pnpm. Run `npm run test` before finishing.\n'}
    result = run(tmp_path, files, times(4, 'pnpm test'))
    assert not any(kind in ('command_in_use_unrecorded', 'verification_command_unnamed') and subject == 'test' for kind, subject in kinds(result))


def test_the_word_test_or_the_manager_in_prose_does_not_name_a_declared_script(tmp_path):
    result = run(tmp_path, {**NODE, 'AGENTS.md': 'Use pnpm and write tests.\n'}, times(3, 'pnpm test'))
    assert row(result, 'verification_command_unnamed')['evidence']['observed_in_sessions']['sessions'] == 3


@pytest.mark.parametrize('text, fires', [('Run relevant tests, lint and typecheck before finishing.', False), ('Keep changes small and reviewed.', True)])
def test_a_harness_that_already_tells_the_agent_to_typecheck_has_recorded_the_practice(tmp_path, text, fires):
    # Found on a real history: four sessions ran `tsc` and the harness said "run ... typecheck" in prose; proposing a line there was noise.
    result = run(tmp_path, {'Makefile': 'build:\n\ttrue\n', 'AGENTS.md': text + '\n'}, times(4, 'tsc --noEmit'))
    assert (('command_in_use_unrecorded', 'typecheck') in kinds(result)) is fires


def test_sessions_that_ran_no_shell_command_add_nothing_and_are_not_clean(tmp_path):
    files = {'Makefile': 'test:\n\tpytest\n', 'AGENTS.md': 'Keep it small.\n'}
    result = run(tmp_path, files, [session('a', 'pytest', tool='Read')] * 4)
    assert result['history']['status'] == 'not_run' and kinds(result) == [('verification_command_unnamed', 'test')]


def test_the_harness_names_one_manager_and_the_sessions_use_another(tmp_path):
    sessions = times(4, 'npm install', 'npm test', prefix='n') + times(1, 'pnpm install', prefix='p')
    found = row(run(tmp_path, {**NODE, 'AGENTS.md': 'Install with `pnpm install`.\n'}, sessions), 'practice_diverges_from_harness')
    assert (found['subject'], found['finding_class'], found['proposed_update']['action']) == ('node', 'contradiction', 'record_practice_or_correct_line')
    assert found['evidence'] == dict(ecosystem='node', named=['pnpm'], used='npm', sessions=4, named_sessions=1, lineage='unknown', lockfile=['pnpm'])
    assert found['where'] == [dict(file='AGENTS.md', line=1)]


@pytest.mark.parametrize('sessions', [
    pytest.param(times(3, 'npm install') + times(2, 'pnpm install', prefix='p'), id='not-dominant'),
    pytest.param(times(2, 'npm install'), id='below-the-session-threshold'),
    pytest.param(times(5, 'pnpm install'), id='sessions-follow-the-harness'),
])
def test_practice_must_dominate_over_enough_sessions(tmp_path, sessions):
    assert not any(kind == 'practice_diverges_from_harness' for kind, _ in kinds(run(tmp_path, {**NODE, 'AGENTS.md': 'Use `pnpm install`.\n'}, sessions)))


def test_a_harness_that_names_no_manager_has_nothing_to_diverge_from(tmp_path):
    assert not any(kind == 'practice_diverges_from_harness' for kind, _ in kinds(run(tmp_path, {**NODE, 'AGENTS.md': 'Keep it small.\n'}, times(5, 'npm install'))))


def test_pip_in_sessions_against_a_harness_that_names_uv(tmp_path):
    files = {'pyproject.toml': '[project]\nname = "x"\n', 'uv.lock': '', 'AGENTS.md': 'Use `uv sync` and `uv run pytest`.\n'}
    found = row(run(tmp_path, files, times(4, 'pip install -e .')), 'practice_diverges_from_harness')
    assert (found['subject'], found['evidence']['used'], found['evidence']['named']) == ('python', 'pip', ['uv'])


def test_a_rule_the_user_stated_in_three_sessions_and_the_harness_lacks(tmp_path):
    sessions = [dict(session=f's{n}', turns=[dict(role='user', text='Never run `npm` here, use pnpm.'), dict(role='agent', text='ok')]) for n in range(3)]
    found = row(run(tmp_path, {'AGENTS.md': 'Keep it small.\n'}, sessions), 'user_rule_unrecorded')
    assert (found['subject'], found['evidence'], found['proposed_update']) == ('forbidden npm', dict(sessions=3, lineage='unknown'), dict(action='record_rule', rule_kind='forbidden'))


def test_a_user_rule_the_harness_already_states_is_not_a_gap(tmp_path):
    sessions = [dict(session=f's{n}', turns=[dict(role='user', text='Never run `npm`.')]) for n in range(4)]
    assert kinds(run(tmp_path, {'AGENTS.md': 'Do not use `npm` in this repository.\n'}, sessions)) == []


def test_a_rule_in_two_sessions_or_a_halt_is_not_a_decision(tmp_path):
    sessions = [dict(session=f's{n}', turns=[dict(role='user', text='Never run `npm`.' if n < 2 else 'stop')]) for n in range(5)]
    assert kinds(run(tmp_path, {'AGENTS.md': 'Keep it small.\n'}, sessions)) == []


def test_a_rule_naming_a_path_or_an_unknown_command_is_user_text_and_gives_no_finding(tmp_path):
    for text in ('Never edit `/home/someone/private/secret-notes.md`.', 'Never run `deploy-payroll --token abc`.'):
        sessions = [dict(session=f's{n}', turns=[dict(role='user', text=text)]) for n in range(4)]
        assert kinds(run(tmp_path / str(abs(hash(text))), {'AGENTS.md': 'Keep it small.\n'}, sessions)) == []


def test_ids_do_not_depend_on_the_counts(tmp_path):
    first = row(run(tmp_path, {'Makefile': 'build:\n\ttrue\n', 'AGENTS.md': 'x\n'}, times(3, 'pytest')), 'command_in_use_unrecorded')
    second = row(run(tmp_path / 'b', {'Makefile': 'build:\n\ttrue\n', 'AGENTS.md': 'x\n'}, times(9, 'pytest', 'pytest')), 'command_in_use_unrecorded')
    assert first['id'] == second['id'] and first['evidence'] != second['evidence']


def test_thresholds_are_in_sessions():
    assert history.MIN_SESSIONS == 3 and history.LINEAGE == 'unknown'
