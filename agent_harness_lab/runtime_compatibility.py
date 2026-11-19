"""Offline, fixture-backed coverage of pinned runtime interfaces, not live runtime compatibility."""
import json
from pathlib import PurePosixPath
import sys

from . import surfaces
from .telemetry import claude_native, codex_rollout

# Pins are deliberately independent of surfaces.PINS: changing a runtime pin must
# not silently turn fixtures recorded for the previous version into evidence.
FIXTURES = {
    'claude-code': {'version': '2.1.317', 'surfaces': (
        ('root-instruction', 'CLAUDE.md', 'instruction', 'adoptable'),
        ('claude-skill-bundle', '.claude/skills/sample/SKILL.md', 'skill', 'adoptable'),
    )},
    'codex': {'version': '0.166.0', 'surfaces': (
        ('root-instruction', 'AGENTS.md', 'instruction', 'adoptable'),
        ('agents-skill-bundle', '.agents/skills/sample/SKILL.md', 'skill', 'adoptable'),
    )},
}
SIGNALS = ('declared_skill_request', 'skill_file_read', 'agent_invocation', 'mcp_invocation', 'memory_attachment', 'hook_output')


def _signals(skill):
    return [dict(name=name, status=skill if name == 'declared_skill_request' else 'untested') for name in SIGNALS]


def _parsers(native):
    return [dict(name='native_capture', status=native), dict(name='generic_jsonl', status='untested')]


def _surface_case(runtime, fixture, rule):
    _, path, kind, writability = fixture
    try:
        matched = rule.match(path, PurePosixPath(path).parts, surfaces._Context(False))
        actual = surfaces.classify(path, runtime)
        return matched and (actual.kind, actual.writability) == (kind, writability)
    except Exception:
        return False


def _codex_capture():
    """Exercise the pinned parser on a synthetic header and tool call, with no runtime process."""
    project = '/synthetic/project'
    rows = (
        dict(type='session_meta', timestamp='2026-01-01T00:00:00Z', ordinal=0,
             payload=dict(id='synthetic', session_id='synthetic', cli_version='0.166.0', cwd=project, history_mode='paginated')),
        dict(type='response_item', timestamp='2026-01-01T00:00:00Z', ordinal=1,
             payload=dict(type='function_call', name='exec_command', call_id='synthetic', arguments='{}')),
    )
    try:
        parser = codex_rollout.Projection(project)
        for index, row in enumerate(rows): parser.add(row, index)
        return [(item['activity'], item.get('tool_name')) for item in parser.rows] == [('session_start', None), ('tool_call', 'exec_command')]
    except Exception:
        return False


def _claude_capture():
    """A declared Skill request in a synthetic pinned-version native message."""
    row = dict(type='assistant', uuid='synthetic', timestamp='2026-01-01T00:00:00Z', version='2.1.317',
               cwd='/synthetic/project', sessionId='synthetic', isSidechain=False,
               message=dict(role='assistant', content=[dict(type='tool_use', id='call', name='Skill', input=dict(skill='example'))]))
    try:
        parser = claude_native.Projection('/synthetic/project', None, lambda: None)
        parser.add(row, 0)
        return (len(parser.rows) == 1 and parser.rows[0]['activity'] == 'tool_call',
                len(parser.rows) == 1 and parser.rows[0].get('skill_signal') == 'declared_invocation')
    except Exception:
        return False, False


def report(runtime, version):
    """Return per-rule fixture outcomes. Unknown versions are unsupported, not assumed compatible."""
    fixture = FIXTURES.get(runtime)
    rules = [row for row in surfaces.RULES if runtime in row.runtimes]
    if runtime not in surfaces.PINS or version != surfaces.PINS[runtime] or fixture is None or version != fixture['version']:
        return dict(runtime=runtime, version=version, status='unsupported', compatibility='unsupported',
                    surface_rules=[dict(rule=row.name, status='untested') for row in rules],
                    capture_parsers=_parsers('untested'), signals=_signals('untested'))
    cases = {case[0]: case for case in fixture['surfaces']}
    invalid = sorted(set(cases) - {row.name for row in rules})
    if len(cases) != len(fixture['surfaces']) or not cases:
        invalid.append('duplicate_or_empty_fixture_set')
    results = [dict(rule=row.name, status=('passed' if _surface_case(runtime, cases[row.name], row) else 'failed')
                    if row.name in cases else 'untested') for row in rules]
    if runtime == 'codex':
        capture = 'passed' if version == codex_rollout.PINNED_VERSION and _codex_capture() else 'failed'
        signal = 'untested'
    else:
        observed = _claude_capture() if version == '2.1.317' and version in claude_native.VERIFIED else (False, False)
        capture, signal = ('passed' if value else 'failed' for value in observed)
    return dict(runtime=runtime, version=version, compatibility='unknown',
                status='failed' if invalid or 'failed' in (capture, signal) or any(row['status'] == 'failed' for row in results) else 'assessed',
                invalid_fixture_rules=invalid,
                surface_rules=results, capture_parsers=_parsers(capture), signals=_signals(signal))


def main():
    results = [report(runtime, version) for runtime, version in sorted(surfaces.PINS.items())]
    print(json.dumps(results, sort_keys=True, indent=2))
    return int(any(row['status'] != 'assessed' for row in results))


if __name__ == '__main__':
    sys.exit(main())
