"""The managed instruction region (#744): marker rules, symlink rules and safe writes, on synthetic files only."""
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import stat
from unittest.mock import patch

import pytest

from agent_harness_lab.exit_guidance import REMEDIATION

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('release_installer', ROOT / 'install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)

START = '<!-- agent-harness-lab:start version=0.1.0 sha256={digest} -->'
END = '<!-- agent-harness-lab:end -->'


def without_region(data):
    """Independent of the installer: the file with the marked region (and its one line break) removed."""
    return re.sub(rb'<!-- agent-harness-lab:start.*?<!-- agent-harness-lab:end -->(\r?\n)?', b'', data, flags=re.S)


def decide(path, version='0.1.0'):
    """inspect_target against the real template shipped by this checkout."""
    digest, body = installer.render(ROOT, version)
    return installer.inspect_target(path, version, digest, body)


def region(body='old text\n', digest=None, eol='\n'):
    digest = digest or hashlib.sha256(body.encode()).hexdigest()
    return eol.join([START.format(digest=digest), *body.splitlines(), END])


def test_template_is_compact_generic_and_carries_only_the_version_placeholder():
    text = (ROOT / installer.TEMPLATE).read_text()
    assert len(text.encode()) <= 1024 and text.count('{version}') == 1
    for required in ('harness-self-improvement', 'ahl improve', 'ahl update --check', 'explicit'):
        assert required in text
    assert not any(token in text for token in ('/', '~', 'http', 'agent-harness-lab:'))  # no paths, URLs or marker text
    assert re.sub(r'\{version\}', '1.2.3', text).count('{') == 0


def test_installer_reasons_equal_the_shared_remediation_table():
    assert installer.REASONS.keys() <= REMEDIATION.keys()
    for reason, fix in installer.REASONS.items():
        assert REMEDIATION[reason] == fix.format(path='the instruction file'), reason
        assert '{' not in REMEDIATION[reason]


@pytest.mark.parametrize('text', [
    pytest.param(f'a\n{region()}\nb\n{region()}\n', id='duplicate'),
    pytest.param(f'a\n{START.format(digest="0" * 64)}\nb\n', id='start-without-end'),
    pytest.param(f'a\n{END}\nb\n', id='end-without-start'),
    pytest.param(f'{END}\n{START.format(digest="0" * 64)}\n', id='end-before-start'),
    pytest.param(f'{START.format(digest="0" * 64)}\n{START.format(digest="0" * 64)}\n{END}\n{END}\n', id='nested'),
    pytest.param(f'text {START.format(digest="0" * 64)}\nbody\n{END}\n', id='start-not-a-whole-line'),
    pytest.param(f'{START.format(digest="0" * 64)}\nbody\n  {END}\n', id='end-indented'),
])
def test_malformed_markers_are_refused_and_nothing_is_guessed(tmp_path, text):
    target = tmp_path / 'AGENTS.md'
    target.write_text(text)
    found = decide(target)
    assert (found['state'], found['reason']) == ('refused', 'instruction_markers_malformed')
    assert 'new' not in found and target.read_text() == text


def test_a_missing_file_is_planned_with_only_the_region_and_mode_0600(tmp_path):
    found = decide(tmp_path / 'CLAUDE.md')
    assert (found['state'], found['old'], found['mode']) == ('absent', None, 0o600)
    assert found['new'].startswith(b'<!-- agent-harness-lab:start version=0.1.0 sha256=') and found['new'].endswith(END.encode() + b'\n')
    assert without_region(found['new']) == b''


@pytest.mark.parametrize('before', [b'', b'# rules\n', b'# no final newline', b'# crlf\r\nline\r\n', b'\xff\xfe not utf-8 \x00\n'])
def test_appending_never_changes_the_bytes_before_the_region(tmp_path, before):
    target = tmp_path / 'AGENTS.md'
    target.write_bytes(before)
    found = decide(target)
    eol = b'\r\n' if b'\r\n' in before else b'\n'
    assert found['state'] == 'absent' and found['new'].startswith(before)
    # The one byte AHL may add outside the markers is the line break a file without a final one needs before the region.
    glue = eol if before and not before.endswith(b'\n') else b''
    assert without_region(found['new']) == before + glue
    assert found['new'].count(eol) == found['new'].count(b'\n')  # one line-ending style throughout


def test_replacing_a_region_keeps_everything_else_including_another_tools_region(tmp_path):
    other = b'<!-- other-tool:start -->\nkeep\n<!-- other-tool:end -->\n'
    before = b'# mine\n' + other + region().encode() + b'\n## after\r\n tail'
    target = tmp_path / 'AGENTS.md'
    target.write_bytes(before)
    found = decide(target)
    assert found['state'] == 'stale' and found['edited'] is False
    assert hashlib.sha256(without_region(found['new'])).digest() == hashlib.sha256(without_region(before)).digest()
    assert other in found['new'] and found['new'].endswith(b'\n## after\r\n tail')


def test_a_current_region_is_a_no_op(tmp_path):
    target = tmp_path / 'AGENTS.md'
    target.write_bytes(b'x\n' + decide(tmp_path / 'absent')['new'])
    assert decide(target)['state'] == 'current'
    assert decide(target, version='0.2.0')['state'] == 'stale'


@pytest.mark.parametrize('case', ['edited-body', 'no-recorded-hash'])
def test_a_hand_edited_or_unrecorded_region_is_stale_and_flagged_as_edited(tmp_path, case):
    target = tmp_path / 'AGENTS.md'
    if case == 'edited-body':
        target.write_text(START.format(digest=hashlib.sha256(b'original\n').hexdigest()) + '\nmy own words\n' + END + '\n')
    else:
        target.write_text('<!-- agent-harness-lab:start -->\nmy own words\n' + END + '\n')
    found = decide(target)
    assert (found['state'], found['edited']) == ('stale', True)


def test_crlf_region_is_rewritten_with_crlf(tmp_path):
    target = tmp_path / 'AGENTS.md'
    target.write_bytes(b'# a\r\n# b\r\n' + region(eol='\r\n').encode() + b'\r\n# c\r\n')
    new = decide(target)['new']
    assert new.count(b'\n') == new.count(b'\r\n') and b'sha256=' in new


@pytest.fixture
def links(tmp_path):
    real = tmp_path / 'dotfiles'
    real.mkdir()
    return tmp_path, real


def test_symlink_to_an_owned_regular_file_is_followed_and_reports_the_resolved_file(links):
    tmp_path, real = links
    (real / 'rules.md').write_text('# mine\n')
    (tmp_path / 'AGENTS.md').symlink_to(real / 'rules.md')
    found = decide(tmp_path / 'AGENTS.md')
    assert found['state'] == 'absent' and found['resolved'] == (real / 'rules.md').resolve()


@pytest.mark.parametrize('case', ['world-writable', 'foreign-owner', 'dangling', 'directory', 'loop'])
def test_symlink_to_anything_else_is_refused_with_the_typed_reason(links, case):
    tmp_path, real = links
    target = real / 'rules.md'
    target.write_text('# mine\n')
    if case == 'world-writable': target.chmod(0o666)
    if case == 'dangling': target.unlink()
    if case == 'directory': target.unlink(); target.mkdir()
    link = tmp_path / 'AGENTS.md'
    link.symlink_to(link if case == 'loop' else target)
    owner = os.geteuid() + 1 if case == 'foreign-owner' else os.geteuid()
    with patch.object(os, 'geteuid', return_value=owner):
        found = decide(link)
    assert (found['state'], found['reason']) == ('refused', 'instruction_symlink_refused')
    assert 'new' not in found


@pytest.mark.parametrize('case', ['directory', 'foreign-owner', 'too-large'])
def test_a_plain_target_that_is_not_an_owned_regular_file_is_refused(tmp_path, case):
    target = tmp_path / 'CLAUDE.md'
    target.mkdir() if case == 'directory' else target.write_text('x')
    owner = os.geteuid() + 1 if case == 'foreign-owner' else os.geteuid()
    with patch.object(os, 'geteuid', return_value=owner), patch.object(installer, 'MAX_INSTRUCTION_BYTES', 0 if case == 'too-large' else 99):
        found = decide(target)
    assert (found['state'], found['reason']) == ('refused', 'instruction_target_unsafe')


def test_write_atomic_preserves_mode_never_widens_and_creates_0600(tmp_path):
    target = tmp_path / 'AGENTS.md'
    target.write_text('old')
    target.chmod(0o640)
    installer.write_atomic(target, b'new', stat.S_IMODE(target.stat().st_mode))
    assert (target.read_bytes(), stat.S_IMODE(target.stat().st_mode)) == (b'new', 0o640)
    created = tmp_path / 'CLAUDE.md'
    installer.write_atomic(created, b'fresh', 0o600, create=True)
    assert (created.read_bytes(), stat.S_IMODE(created.stat().st_mode)) == (b'fresh', 0o600)
    with pytest.raises(FileExistsError):
        installer.write_atomic(created, b'raced', 0o600, create=True)
    assert created.read_bytes() == b'fresh' and sorted(p.name for p in tmp_path.iterdir()) == ['AGENTS.md', 'CLAUDE.md']


def test_targets_follow_the_agent_selection_and_never_invent_a_directory_or_the_shared_file(tmp_path, monkeypatch):
    home = Path(os.environ['HOME'])
    (home / '.claude').mkdir()
    rows = {(label, path.name, skip) for label, path, skip in installer.instruction_targets('both')}
    assert rows == {('claude', 'CLAUDE.md', None), ('codex', 'AGENTS.md', 'config_directory_missing')}  # no shared file yet
    (home / '.agents').mkdir()
    (home / '.agents/AGENTS.md').write_text('shared')
    assert [label for label, _, _ in installer.instruction_targets('codex')] == ['codex', 'shared']
    assert [label for label, _, _ in installer.instruction_targets('claude')] == ['claude']
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(tmp_path / 'claude-elsewhere'))
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex-elsewhere'))
    elsewhere = {path.parent.name: skip for _, path, skip in installer.instruction_targets('both')}
    assert elsewhere['claude-elsewhere'] == elsewhere['codex-elsewhere'] == 'config_directory_missing'
    assert not (tmp_path / 'claude-elsewhere').exists() and not (home / '.codex').exists()


def test_a_symlinked_codex_file_and_the_shared_file_are_one_target(tmp_path):
    home = Path(os.environ['HOME'])
    (home / '.agents').mkdir(); (home / '.codex').mkdir()
    (home / '.agents/AGENTS.md').write_text('shared')
    (home / '.codex/AGENTS.md').symlink_to(home / '.agents/AGENTS.md')
    assert len(installer.instruction_targets('codex')) == 1


def test_settings_prefer_explicit_then_recorded_then_the_skill_directories_of_an_older_receipt():
    home = Path(os.environ['HOME'])
    assert installer.instruction_settings({}, None, None) == (True, 'both')
    assert installer.instruction_settings({'instructions': {'enabled': False, 'agent': 'claude'}}) == (False, 'claude')
    assert installer.instruction_settings({'instructions': {'enabled': False, 'agent': 'claude'}}, 'codex', True) == (True, 'codex')
    older = lambda *folders: dict(skill_dirs=[str(home / folder) for folder in folders])
    assert installer.instruction_settings(older('.agents/skills')) == (True, 'codex')
    assert installer.instruction_settings(older('.claude/skills')) == (True, 'claude')
    assert installer.instruction_settings(older('.claude/skills', '.agents/skills')) == (True, 'both')
    assert installer.instruction_settings(older('somewhere/else')) == (True, 'both')
