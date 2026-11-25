"""The commands the agent ran in native sessions, as counts of closed-vocabulary keys per session (#1117).

A key is a script or target name the project itself declares, a known verification tool, or `cargo`/`go` with a build or test
subcommand; a package-manager use is counted apart. An argument, path, URL, secret or one-off program never becomes part of
either (those lines are only counted as `other`). Counts are per session (how many sessions ran it at least once) beside total
uses. Native captures carry no verified lineage, so sessions are separate reads, never independent roots, and the report says
so. A read with no shell command is `not_run`, never an empty profile.
"""
from collections import Counter

from agent_harness_lab.diagnostics.instruction_rules import SHELL_TOOLS
from agent_harness_lab.project_profile import commands

PROFILE = 'ahl-session-commands-v1'
TOP_KEYS = 40
MANAGER_SUBCOMMANDS = frozenset('install i ci add remove uninstall update upgrade sync lock run exec test start build dev lint'.split())
TOOL_SUBCOMMANDS = dict(cargo=frozenset('build test check clippy fmt nextest bench'.split()), go=frozenset('build test vet fmt'.split()))
TOOL_KINDS = frozenset(('test_runner', 'linter', 'formatter', 'type_checker'))
ECOSYSTEM_OF = {**{name: 'node' for name in commands.NODE_MANAGERS}, **{name: 'python' for name in (*commands.PYTHON_MANAGERS, 'pip')}}


def observe(line, declared):
    """(managers, keys) of one shell line: the package managers it runs and the closed-vocabulary command keys it holds."""
    managers, found = [], []
    for command in commands.simple_commands(line):
        inner = commands.unwrapped(command)
        for item in (command, inner) if inner is not command else (command,):
            sub = next((word for word in item['args'] if not word.startswith('-')), '')
            name = 'pip' if item['program'] == 'pip3' else item['program']
            if name in ECOSYSTEM_OF and sub in MANAGER_SUBCOMMANDS: managers.append(name)
        pair = commands.runner_target(command, any_directory=True)
        sub = next((word for word in command['args'] if not word.startswith('-')), '')
        if pair and pair[1] in declared:
            found.append(dict(key=' '.join(pair), program=pair[0], target=pair[1], category=commands.category(pair[1])))
        elif commands.KIND.get(inner['program']) in TOOL_KINDS:
            found.append(dict(key=inner['program'], program=inner['program'], target=None, category=commands.category_of(command)))
        elif sub in TOOL_SUBCOMMANDS.get(command['program'], ()):
            found.append(dict(key=f"{command['program']} {sub}", program=command['program'], target=sub,
                              category=commands.category(sub, f"{command['program']} {sub}")))
    return managers, found


def declared_names(profile):
    return {name for runner in profile['runners'].values() for name in (*runner['targets'], *runner.get('nested_targets', ()))}


def summarize(sessions, profile):
    """The private command profile of the read sessions (dicts with `session` and `turns`), counted per session."""
    declared = declared_names(profile)
    keyed, categories, manager_sessions, uses, other, with_commands = {}, Counter(), {}, Counter(), 0, 0
    for session in sessions:
        keys, kinds, used = set(), set(), False
        for turn in session['turns']:
            if turn['role'] != 'tool_call' or turn.get('tool') not in SHELL_TOOLS or not turn.get('command'): continue
            used, keyed_call = True, False
            for line in turn['command'].splitlines():
                managers, found = observe(line, declared)
                keyed_call = keyed_call or bool(managers or found)
                for name in managers:
                    uses['manager:' + name] += 1
                    manager_sessions.setdefault(name, set()).add(session['session'])
                for row in found:
                    uses[row['key']] += 1
                    keyed.setdefault(row['key'], row)
                    keys.add(row['key'])
                    if row['category']: kinds.add(row['category']); uses['category:' + row['category']] += 1
            other += not keyed_call
        with_commands += used
        for key in keys: uses['sessions:' + key] += 1
        categories.update(kinds)
    if not with_commands:
        return dict(profile=PROFILE, status='not_run', reason='no_shell_command_in_read_sessions', sessions_read=len(sessions), publication_allowed=False)
    rows = sorted(keyed.values(), key=lambda row: (-uses['sessions:' + row['key']], -uses[row['key']], row['key']))
    return dict(profile=PROFILE, status='completed', sessions_read=len(sessions), sessions_with_commands=with_commands, lineage='unknown',
                calls_without_key=other, keys_total=len(rows), authority='descriptive_only', publication_allowed=False,
                commands=[dict(key=row['key'], program=row['program'], target=row['target'], category=row['category'],
                               sessions=uses['sessions:' + row['key']], uses=uses[row['key']]) for row in rows[:TOP_KEYS]],
                categories={name: dict(sessions=count, uses=uses['category:' + name]) for name, count in sorted(categories.items())},
                managers=[dict(ecosystem=ECOSYSTEM_OF[name], name=name, sessions=len(found), uses=uses['manager:' + name])
                          for name, found in sorted(manager_sessions.items())])
