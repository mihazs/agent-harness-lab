"""`ahl analyze` compares the commands the agent ran in native sessions with the harness (#1117, #1118); synthetic stores only."""
import json

import pytest

from tests.integration.diagnostics.test_adherence_analysis import analyze, capture, home  # noqa: F401  (the shared synthetic store fixture)
from tests.support.profile_fixture import build, package

SECRET = 'zz-private-token'


def session(number):
    return f'00000000-0000-4000-8000-00000000000{number}'


def plant(project, instruction, scripts=None):
    build(project.parent, {'package.json': package(scripts or {'build': 'vite build'}), 'pnpm-lock.yaml': '', 'AGENTS.md': instruction}, name=project.name)


def run_sessions(project, count, *commands):
    for number in range(1, count + 1):
        steps = [('user', f'Please do task {SECRET} {number}.'), *(('call', 'Bash', dict(command=line)) for line in commands), ('agent', 'Done.')]
        capture(project, steps, session=session(number))


def findings(brief):
    return {row['kind']: row for row in brief['harness_profile']['findings']}


def test_three_sessions_against_a_harness_that_names_another_manager_are_a_contradiction_hypothesis(home):  # noqa: F811
    plant(home, 'Install with `pnpm install` and keep changes small.\n')
    run_sessions(home, 3, 'npm install', f'pytest -q tests/{SECRET}.py', 'git status')
    folder, brief = analyze(home)
    found = findings(brief)
    assert found['practice_diverges_from_harness']['evidence'] == dict(ecosystem='node', named=['pnpm'], used='npm', sessions=3, named_sessions=0,
                                                                          lineage='unknown', lockfile=['pnpm'])
    assert found['command_in_use_unrecorded']['subject'] == 'test'
    view = brief['harness_profile']['session_commands']
    assert (view['status'], view['sessions_read'], view['sessions_with_commands'], view['lineage']) == ('completed', 3, 3, 'unknown')
    assert {row['key']: row['sessions'] for row in view['top']} == {'pytest': 3}
    private = json.loads((folder / 'harness-profile.json').read_text())
    assert private['history']['managers'] == [dict(ecosystem='node', name='npm', sessions=3, uses=3)]
    for text in (json.dumps(brief), json.dumps(private)):
        assert SECRET not in text and str(home) not in text and 'git status' not in text


def test_two_sessions_make_no_history_finding_and_say_what_was_read(home):  # noqa: F811
    plant(home, 'Install with `pnpm install`. Run `pnpm test`; lint with eslint; build with vite.\n', {'test': 'vitest run', 'lint': 'eslint .', 'build': 'vite build'})
    run_sessions(home, 2, 'npm install', 'pytest')
    _, brief = analyze(home)
    assert brief['harness_profile']['findings'] == [] and brief['harness_profile']['session_commands']['sessions_read'] == 2


def test_sessions_without_a_shell_command_are_not_a_clean_result(home):  # noqa: F811
    plant(home, 'Keep changes small.\n')
    for number in range(1, 4):
        capture(home, [('user', 'go'), ('call', 'Read', dict(file_path=str(home / 'AGENTS.md'))), ('agent', 'ok')], session=session(number))
    _, brief = analyze(home)
    view = brief['harness_profile']['session_commands']
    assert (view['status'], view['reason'], view['sessions_read']) == ('not_run', 'no_shell_command_in_read_sessions', 3)
    assert not any(kind in findings(brief) for kind in ('command_in_use_unrecorded', 'practice_diverges_from_harness'))


def test_no_native_capture_leaves_the_static_findings_alone(home):  # noqa: F811
    plant(home, 'Keep changes small.\n')
    _, brief = analyze(home)
    assert brief['harness_profile']['session_commands']['status'] == 'not_run'
    assert brief['harness_profile']['session_commands']['reason'] == 'no_native_session_read'
    assert 'toolchain_not_stated' in findings(brief)


@pytest.mark.parametrize('text', ['Never run `docker` here.'])
def test_a_rule_the_user_repeats_in_three_sessions_and_the_harness_lacks_is_recorded_as_a_gap(home, text):  # noqa: F811
    plant(home, 'Use pnpm.\n')
    for number in range(1, 4):
        capture(home, [('user', text), ('agent', 'ok')], session=session(number))
    _, brief = analyze(home)
    assert findings(brief)['user_rule_unrecorded']['subject'] == 'forbidden docker'
