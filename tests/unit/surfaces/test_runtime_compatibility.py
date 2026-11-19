"""The report exercises fixtures and keeps unobserved behavior unknown."""
from copy import deepcopy
import json

from agent_harness_lab import runtime_compatibility as compatibility, surfaces


def status(result, group, name):
    return next(row['status'] for row in result[group] if row['name'] == name)


def test_current_pins_have_executable_fixture_results_and_explicit_gaps(capsys):
    assert compatibility.main() == 0
    first = capsys.readouterr().out
    assert compatibility.main() == 0
    assert capsys.readouterr().out == first
    reports = {row['runtime']: row for row in json.loads(first)}
    for runtime, result in reports.items():
        assert result['version'] == surfaces.PINS[runtime]
        assert result['compatibility'] == 'unknown' and result['invalid_fixture_rules'] == []
        assert {row['rule'] for row in result['surface_rules']} == {rule.name for rule in surfaces.RULES if runtime in rule.runtimes}
        assert 'passed' in {row['status'] for row in result['surface_rules']}
        assert status(result, 'signals', 'skill_file_read') == 'untested'
    assert status(reports['codex'], 'capture_parsers', 'native_capture') == 'passed'
    assert status(reports['claude-code'], 'capture_parsers', 'native_capture') == 'passed'
    assert status(reports['claude-code'], 'signals', 'declared_skill_request') == 'passed'
    assert status(reports['codex'], 'signals', 'declared_skill_request') == 'untested'


def test_unknown_runtime_and_version_are_unsupported():
    for runtime, version in [('codex', '0.0.0'), ('unknown', '0.166.0')]:
        result = compatibility.report(runtime, version)
        assert result['status'] == 'unsupported'
        assert status(result, 'capture_parsers', 'native_capture') == status(result, 'signals', 'declared_skill_request') == 'untested'
        assert all(row['status'] == 'untested' for row in result['surface_rules'])


def test_pin_bump_without_matching_fixtures_fails_and_names_uncovered_rule(monkeypatch, capsys):
    monkeypatch.setitem(surfaces.PINS, 'codex', '999.0.0')
    assert compatibility.main() == 1
    result = next(row for row in json.loads(capsys.readouterr().out) if row['runtime'] == 'codex')
    assert result['status'] == 'unsupported'
    assert any(row == {'rule': 'root-instruction', 'status': 'untested'} for row in result['surface_rules'])


def test_broken_fixture_is_failed_not_counted_as_coverage(monkeypatch):
    fixture = deepcopy(compatibility.FIXTURES['codex'])
    fixture['surfaces'] = (('root-instruction', 'AGENTS.md', 'skill', 'adoptable'),)
    monkeypatch.setitem(compatibility.FIXTURES, 'codex', fixture)
    result = compatibility.report('codex', surfaces.PINS['codex'])
    assert result['status'] == 'failed'
    assert next(row['status'] for row in result['surface_rules'] if row['rule'] == 'root-instruction') == 'failed'
    assert next(row['status'] for row in result['surface_rules'] if row['rule'] == 'agents-skill-bundle') == 'untested'


def test_parser_failure_is_reported(monkeypatch):
    def broken(*args, **kwargs):
        raise ValueError('synthetic parser failure')
    monkeypatch.setattr(compatibility.codex_rollout.Projection, 'add', broken)
    result = compatibility.report('codex', surfaces.PINS['codex'])
    assert status(result, 'capture_parsers', 'native_capture') == 'failed' and result['status'] == 'failed'


def test_parser_dropping_its_pinned_version_is_a_failure(monkeypatch):
    monkeypatch.setattr(compatibility.codex_rollout, 'PINNED_VERSION', '999.0.0')
    result = compatibility.report('codex', surfaces.PINS['codex'])
    assert status(result, 'capture_parsers', 'native_capture') == 'failed' and result['status'] == 'failed'


def test_fixture_for_a_missing_rule_cannot_silently_disappear(monkeypatch):
    fixture = deepcopy(compatibility.FIXTURES['codex'])
    fixture['surfaces'] += (('removed-rule', 'AGENTS.md', 'instruction', 'adoptable'),)
    monkeypatch.setitem(compatibility.FIXTURES, 'codex', fixture)
    result = compatibility.report('codex', surfaces.PINS['codex'])
    assert result['status'] == 'failed' and result['invalid_fixture_rules'] == ['removed-rule']


def test_claude_parser_must_accept_the_fixture_version(monkeypatch):
    monkeypatch.setattr(compatibility.claude_native, 'VERIFIED', ('2.1.316',))
    result = compatibility.report('claude-code', surfaces.PINS['claude-code'])
    assert status(result, 'capture_parsers', 'native_capture') == 'failed' and result['status'] == 'failed'
