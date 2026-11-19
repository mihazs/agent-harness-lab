"""Help and README state the improve/watch recommendation boundary without running anything."""
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]

# Exact help snapshots: the lines that say what each command changes and never changes.
TOP_DESCRIPTION = ('Start with ahl analyze . for read-only analysis, or ahl improve . to stage, evaluate and install the\n'
                   'winning harness candidate in one call. AHL itself writes private state and, for a winning candidate, a journaled install with a rollback\n'
                   'command; a project ahl.json, watch and user-level scopes keep candidates as recommendations, and the cooperative agent is not sandboxed.\n')
SUMMARIES = {
    'improve': 'Stage and evaluate harness candidates, then install the winning one through a rollback journal '
               '(a project ahl.json keeps it a recommendation)',
    'campaign': 'Create, inspect and cancel private planned campaigns offline; execution is not implemented',
    'watch': 'Recheck evidence; rerun recommendation-only staging and evaluation on a first pass, changed evidence '
             'or configuration, or a classified transient retry; AHL never writes harness files',
}
BOUNDARIES = {
    'improve': 'Changes: private state only: run artifacts (staged baseline/candidate copies, evaluation results, change plan) and a run lock.\n'
               'A winning candidate is installed through a rollback journal; the receipt names the changed files and the ahl rollback command.\n'
               'Installing is not certification. A project ahl.json, a user-level scope and watch keep the candidate a recommendation.\n'
               'The agent is cooperative: read-only project access is instructed, not enforced; its own permission settings apply.\n'
               'AHL rechecks the project harness before recommending and rejects a stale result; it does not revert that change.\n'
               '--repair-contracts applies only a bounded skill-header repair, without model calls, through the same journal.\n'
               'No commit, publish, deploy or background service.\n',
    'watch': 'Changes: private state only: run artifacts, a run lock and the watch checkpoint.\n'
             'AHL never writes project harness files; watch has no --repair-contracts and applies no candidate.\n'
             'Each pass uses the same cooperative agent as improve: read-only project access is instructed, not enforced.\n'
             'Reruns: on changed evidence or execution configuration, or a classified transient retry.\n',
}
# Option snapshots guard against dropping options while rewording help.
COMMON = ('--help', '--events', '--source-root', '--session', '--max-captures', '--max-capture-scan', '--max-events',
          '--sequence-length', '--full-detail')
RUNTIME = ('--format', '--source-version', '--output')
EXECUTION = ('--focus', '--agent', '--inference-settings', '--codex', '--claude', '--timeout', '--inherit-env', '--with-user-harness', '--sandbox-graders',
             '--cgroup-root', '--image-root', '--image-sha256', '--permission-audit', '--evaluation-store', '--disk-bytes')
OPTIONS = {
    'improve': (*COMMON, '--history-from', '--incident-corpus', '--incident-query', '--history-store', '--history-run', *RUNTIME, '--memory-store', '--incremental', '--explain-reuse', '--experiment-request', '--from-audit', '--history', '--since', '--source', '--correction-excerpts', *EXECUTION,
                '--resume', '--construction-fraction', '--regression-margin', '--area', '--repair-contracts', '--scope', '--remember-runtime',
                '--accept-score', '--max-rounds', '--plateau-rounds', '--on-regression', '--single-pass'),
    'watch': (*COMMON, *RUNTIME, *EXECUTION, '--interval', '--max-attempts', '--retry-delay', '--watch-budget', '--once'),
}
SUBCOMMANDS = ('rollback', 'update', 'mine', 'diagnose', 'evaluate', 'benchmark', 'skills', 'bank', 'bank-interchange',
               'bank-review', 'experiment', 'experiment-history', 'grader-correction', 'regression', 'regression-review', 'campaign',
               'sandbox-check', 'research-simulate', 'incident-search', 'promotion', 'apply', 'annotation-plan', 'instruction-adherence', 'instruction-repair', 'compare', 'score', 'score-sessions', 'trace-import', 'trace-export', 'adapters', 'hypothesis-probe', 'benchmark-report', 'doctor', 'audit', 'init',
               'sources', 'memory', 'config', 'analyze', 'analysis', 'improve', 'watch')
# Wording that would imply behavioral auto-adoption or continuous self-modification.
FORBIDDEN = ('adopt supported harness repairs', 'continuously improve', 'automatic improvement', 'automatically apply',
             'auto-adopt', 'adopts')
# Unqualified guarantees about the whole command: the cooperative agent is not sandboxed, so only what AHL
# itself writes can be promised.
UNQUALIFIED = ('never changes:', 'never change:', 'never edits', 'never apply behavioral changes to the project',
               'only when evidence or configuration changes')
README_BOUNDARY = (
    '`ahl improve .` stages baseline and candidate copies, evaluation results, a change plan and a run lock as private state.',
    'installs the evaluated candidate into the project through a rollback journal and ends `applied`',
    'It is told to treat the project as read-only, but nothing enforces that; your agent\'s own permission '
    'settings decide whether it can write there.',
    'AHL rechecks the project harness before it recommends and rejects a stale result, but it does not revert '
    'such a change.',
    'Installing is your request, not certification',
    '`ahl improve . --repair-contracts` applies only a bounded skill-header repair',
    '`ahl.json` keeps even that repair recommendation-only.',
    '`ahl watch .` reruns the same recommendation-only staging and evaluation when evidence or execution '
    'configuration changes, or after a classified transient failure.',
    'has no `--repair-contracts` option and never writes harness files itself; its agent is cooperative in the same way.',
    'Neither command commits, publishes, deploys or starts a background service.',
)


def flat(text):
    return ' '.join(text.split())


COMMANDS = ('top', 'improve', 'watch')
DOCS = ('README.md', 'docs/cli-help.md', 'docs/workflows.md', 'docs/command-reference.md')


@pytest.fixture(scope='module')
def captured(tmp_path_factory):
    outputs = {}
    base = tmp_path_factory.mktemp('help-boundary')
    work, home, bin_dir = base / 'work', base / 'home', base / 'bin'
    for path in (work, home, bin_dir):
        path.mkdir()
    marker = base / 'agent-started'
    # Fake agent runtimes record any invocation; help must start none.
    for name in ('codex', 'claude'):
        fake = bin_dir / name
        fake.write_text(f'#!/bin/sh\necho "$0" >> {marker}\n')
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    env = {**os.environ, 'PYTHONPATH': str(ROOT), 'HOME': str(home), 'XDG_STATE_HOME': str(home / 'state'),
           'PATH': str(bin_dir) + os.pathsep + os.environ.get('PATH', ''), 'COLUMNS': '100'}
    for route in ((), ('improve',), ('watch',)):
        run = subprocess.run([sys.executable, '-m', 'agent_harness_lab', *route, '--help'], cwd=work, env=env,
                             capture_output=True, text=True, timeout=30)
        outputs[route[0] if route else 'top'] = run
    side_effects = dict(agent=marker.exists(), work=os.listdir(work), home=os.listdir(home))
    return outputs, side_effects


@pytest.fixture(scope='module')
def outputs(captured):
    return captured[0]


@pytest.mark.parametrize('name', [pytest.param(name, id=name) for name in COMMANDS])
def test_help_exits_zero(outputs, name):
    run = outputs[name]
    assert run.returncode == 0, run.stderr
    assert run.stderr == ''


def test_help_starts_no_agent_and_writes_no_state(captured):
    outputs, side_effects = captured
    assert tuple(outputs) == COMMANDS
    assert side_effects == dict(agent=False, work=[], home=[])


def test_top_level_help_snapshot_states_the_boundary_and_keeps_every_subcommand(outputs):
    text = outputs['top'].stdout
    assert TOP_DESCRIPTION in text
    for name, summary in SUMMARIES.items():
        assert re.search(re.escape(name) + r' ' + re.escape(flat(summary)), flat(text))
    choices = re.search(r'\{([a-z,-]+)\}', text).group(1).split(',')
    assert tuple(choices) == SUBCOMMANDS


@pytest.mark.parametrize('name', [pytest.param(name, id=name) for name in ('improve', 'watch')])
def test_command_help_snapshot_states_what_changes_and_never_changes(outputs, name):
    text = outputs[name].stdout
    assert '\n' + SUMMARIES[name] + '\n' in text
    assert '\n\n' + BOUNDARIES[name] + 'Output: ' in text


@pytest.mark.parametrize('name,expected', [pytest.param(name, expected, id=name) for name, expected in OPTIONS.items()])
def test_command_help_keeps_every_option(outputs, name, expected):
    options = re.findall(r'^  (?:-h, )?(--[a-z0-9-]+)', outputs[name].stdout, re.M)
    assert tuple(options) == expected


@pytest.mark.parametrize('name,phrase', [pytest.param(name, phrase, id=f'{name}-{phrase}')
                                         for name in COMMANDS for phrase in FORBIDDEN])
def test_help_never_implies_behavioral_auto_adoption(outputs, name, phrase):
    text = flat(outputs[name].stdout).lower()
    assert phrase not in text


@pytest.mark.parametrize('name,phrase', [pytest.param(name, phrase, id=f'{name}-{phrase}')
                                         for name in (*COMMANDS, *DOCS) for phrase in UNQUALIFIED])
def test_help_and_docs_promise_only_what_ahl_itself_writes(outputs, name, phrase):
    text = outputs[name].stdout if name in outputs else (ROOT / name).read_text(encoding='utf-8')
    assert not (phrase in flat(text).lower()), f'{name} states an unqualified {phrase!r}'


@pytest.mark.parametrize('name', [pytest.param(name, id=name) for name in ('improve', 'watch')])
def test_command_help_states_the_cooperative_agent_boundary(outputs, name):
    assert 'read-only project access is instructed, not enforced' in flat(outputs[name].stdout)


@pytest.mark.parametrize('claim', [pytest.param(claim, id=claim[:40]) for claim in README_BOUNDARY])
def test_readme_states_the_same_boundary_for_improve_and_watch(claim):
    readme = flat((ROOT / 'README.md').read_text(encoding='utf-8'))
    assert claim in readme
