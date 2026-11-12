"""The audit annotation converter treats every report value as hostile text (#392); synthetic data only."""
import pytest

from agent_harness_lab.audit.annotations import LIMIT, lines

HOSTILE = ['a\n::error::forged', 'x%0A::error::y', 'f,title=forged', 'a:b::c', 'cr\rlf\n', '::stop-commands::t', 'nul\x00\x1b[31m']


def report(path, **extra):
    row = dict(path=path, reason='instruction_empty', remediation='add_instructions', dimension='instructions', status='weak')
    return dict(status='completed', findings=[dict(row, **extra)])


@pytest.mark.parametrize('name', HOSTILE)
def test_hostile_text_cannot_start_or_alter_a_command(name):
    out = lines(report(name, reason=name, remediation=name, dimension=name))
    assert len(out) == 1 and '\n' not in out[0] and '\r' not in out[0]
    head = out[0].split('::', 2)[1]  # "warning file=...,title=..."
    assert out[0].startswith('::warning file=') and out[0].count('::') == 2 and out[0].split('::', 2)[2].count('::') == 0
    properties = head.split(' ', 1)[1].split(',')
    assert [p.split('=', 1)[0] for p in properties] == ['file', 'title'] and ':' not in head
    assert '\x00' not in out[0] and '\x1b' not in out[0]


def test_a_forged_error_is_data_not_a_second_command():
    assert all(line.startswith('::warning ') for line in lines(report('a\n::error::forged')))


def test_project_level_finding_has_no_file_and_changed_filter_applies():
    assert lines(report('.'))[0].startswith('::warning title=')
    assert lines(report('docs/a.md'), changed={'other.md'}) == []
    assert len(lines(report('docs/a.md'), changed={'docs/a.md'})) == 1


def test_incomplete_audit_is_one_error_without_findings():
    out = lines(dict(status='blocked', findings=[dict(path='a', reason='r')]))
    assert len(out) == 1 and out[0].startswith('::error ')


def test_annotations_are_bounded_and_never_claim_performance():
    out = lines(dict(status='completed', findings=report('f')['findings'] * (LIMIT + 5)))
    assert len(out) == LIMIT + 1 and out[-1].startswith('::notice ')
    assert all('not evidence of harness performance' in line for line in out[:-1])
