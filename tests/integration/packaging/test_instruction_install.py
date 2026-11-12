"""Install, update, rollback and doctor of the managed instruction region (#744), with a temporary HOME and no network."""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent_harness_lab import installation, updater
from agent_harness_lab.onboarding import doctor
from tests.integration.packaging import test_release_install as base

installer, rel = base.installer, base.rel  # the synthetic-release fixture is shared, not copied


def without_region(data):
    return re.sub(rb'<!-- agent-harness-lab:start.*?<!-- agent-harness-lab:end -->(\r?\n)?', b'', data, flags=re.S)


def digest(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def homes(tmp_path, monkeypatch):
    """Claude and Codex configuration directories that exist, one with a hand-written file."""
    claude, codex = tmp_path / 'claude-config', tmp_path / 'codex-home'
    claude.mkdir(); codex.mkdir()
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(claude))
    monkeypatch.setenv('CODEX_HOME', str(codex))
    (claude / 'CLAUDE.md').write_bytes(b'# my private rules\nkeep me\n')
    return SimpleNamespace(claude=claude / 'CLAUDE.md', codex=codex / 'AGENTS.md', shared=Path(os.environ['HOME']) / '.agents/AGENTS.md')


def built(rel, version):
    """One archive per version: a rebuilt archive has a different gzip header and so a different checksum."""
    cache = rel.__dict__.setdefault('built', {})
    if version not in cache:
        cache[version] = rel.release(version)
    return cache[version]


def run(rel, version='0.1.0', settings=(True, 'both')):
    archive, checksums, _ = built(rel, version)
    rows = []
    destination, backup = installer.install(archive, checksums, rel.install_root, rel.bin, rel.dirs, settings, rows)
    return SimpleNamespace(destination=destination, backup=backup, rows={row['agent']: row for row in rows}, archive=archive, checksums=checksums)


def test_first_install_adds_the_region_after_the_user_bytes_and_a_repeat_writes_nothing(rel, homes):
    before = homes.claude.read_bytes()
    first = run(rel)
    after = homes.claude.read_bytes()
    assert after.startswith(before) and without_region(after) == before
    assert (first.rows['claude']['state'], first.rows['codex']['state']) == ('updated', 'created')
    assert stat.S_IMODE(homes.codex.stat().st_mode) == 0o600 and without_region(homes.codex.read_bytes()) == b''
    assert b'version=0.1.0 sha256=' in after and after.endswith(b'<!-- agent-harness-lab:end -->\n')
    stamps = [(p.stat().st_ino, p.stat().st_mtime_ns) for p in (homes.claude, homes.codex)]
    again = run(rel)
    assert [row['state'] for row in again.rows.values()] == ['current', 'current']
    assert [(p.stat().st_ino, p.stat().st_mtime_ns) for p in (homes.claude, homes.codex)] == stamps
    assert again.backup is None  # no write and no backup
    assert len(list(rel.install_root.glob('backups/*/instruction-*'))) == 1  # only the first install's copy of the Claude file


def test_the_first_install_backs_up_a_file_it_changes_and_not_one_it_creates(rel, homes):
    first = run(rel)
    assert (first.backup / 'instruction-0').read_bytes() == b'# my private rules\nkeep me\n'
    assert sorted(p.name for p in first.backup.glob('instruction-*')) == ['instruction-0']
    assert first.rows['claude']['backup'] == str(first.backup / 'instruction-0') and 'backup' not in first.rows['codex']


def test_update_replaces_only_the_region_even_beside_another_tools_region(rel, homes):
    other = b'<!-- other-tool:start -->\nkeep\n<!-- other-tool:end -->\n'
    homes.claude.write_bytes(b'# top\r\n' + other + b'\xff binary tail')
    first = run(rel, '0.1.0')
    old = homes.claude.read_bytes()
    second = run(rel, '0.2.0')
    new = homes.claude.read_bytes()
    assert second.rows['claude']['state'] == 'updated' and b'version=0.2.0' in new and b'version=0.1.0' not in new
    assert digest(without_region(old)) == digest(without_region(new)) and other in new
    assert (second.backup / second.rows['claude']['backup'].rsplit('/', 1)[1]).read_bytes() == old
    assert first.backup is not None


def test_a_hand_edited_region_is_replaced_with_a_backup_and_the_typed_note(rel, homes):
    run(rel)
    mine = homes.claude.read_bytes().replace(b'Never hand-edit', b'I changed this: never hand-edit')
    homes.claude.write_bytes(mine)
    second = run(rel, '0.1.0')
    row = second.rows['claude']
    assert row['state'] == 'updated' and row['notes'] == ['region_edited_by_user_replaced']
    assert b'I changed this' not in homes.claude.read_bytes()
    assert Path(row['backup']).read_bytes() == mine


@pytest.mark.parametrize('text', [b'a\n<!-- agent-harness-lab:start -->\nb\n', b'<!-- agent-harness-lab:end -->\n', b'x <!-- agent-harness-lab:start -->\n'])
def test_malformed_markers_leave_the_file_alone_and_the_launcher_and_links_complete(rel, homes, text):
    homes.codex.write_bytes(text)
    result = run(rel)
    row = result.rows['codex']
    assert (row['state'], row['reason']) == ('refused', 'instruction_markers_malformed')
    assert 'AGENTS.md' in row['fix'] and 'ahl update' in row['fix']
    assert homes.codex.read_bytes() == text
    assert (rel.bin / 'ahl').resolve() == result.destination / 'ahl' and result.rows['claude']['state'] == 'updated'
    assert all((folder / name).resolve() == result.destination / 'skills' / name for folder in rel.dirs for name in installer.SKILLS)


def test_a_symlinked_target_is_followed_to_an_owned_file_and_reported(rel, homes, tmp_path):
    real = tmp_path / 'dotfiles'; real.mkdir()
    (real / 'agents.md').write_bytes(b'# mine\n')
    homes.codex.symlink_to(real / 'agents.md')
    row = run(rel).rows['codex']
    assert homes.codex.is_symlink() and row['notes'] == ['symlink_followed']
    assert row['resolved'] == str((real / 'agents.md').resolve()) and b'agent-harness-lab:start' in (real / 'agents.md').read_bytes()


def test_a_symlink_to_a_world_writable_file_is_refused_with_no_change(rel, homes, tmp_path):
    outside = tmp_path / 'shared.md'; outside.write_bytes(b'# not mine to edit\n'); outside.chmod(0o666)
    homes.codex.symlink_to(outside)
    row = run(rel).rows['codex']
    assert (row['state'], row['reason']) == ('refused', 'instruction_symlink_refused')
    assert outside.read_bytes() == b'# not mine to edit\n' and homes.codex.is_symlink()


def test_mode_is_preserved_and_a_missing_directory_is_skipped_without_creating_it(rel, homes, tmp_path):
    homes.claude.chmod(0o640)
    homes.codex.parent.rmdir()
    result = run(rel)
    assert stat.S_IMODE(homes.claude.stat().st_mode) == 0o640
    assert (result.rows['codex']['state'], result.rows['codex']['reason']) == ('skipped', 'config_directory_missing')
    assert not homes.codex.parent.exists()


def test_the_shared_file_is_a_target_only_when_it_already_exists(rel, homes):
    assert 'shared' not in run(rel).rows
    homes.shared.parent.mkdir(parents=True, exist_ok=True)
    homes.shared.write_bytes(b'# shared\n')
    assert run(rel).rows['shared']['state'] == 'updated'


def test_a_single_agent_selection_leaves_the_other_files_alone(rel, homes):
    result = run(rel, settings=(True, 'claude'))
    assert list(result.rows) == ['claude'] and not homes.codex.exists()


def test_installing_without_a_selection_never_touches_instruction_files(rel, homes):
    before = homes.claude.read_bytes()
    archive, checksums, _ = built(rel, '0.1.0')
    installer.install(archive, checksums, rel.install_root, rel.bin, rel.dirs)
    assert homes.claude.read_bytes() == before and not homes.codex.exists()


def test_a_write_failure_is_a_warning_row_not_an_exception_and_the_receipt_is_committed(rel, homes):
    with patch.object(installer, 'write_atomic', side_effect=OSError('synthetic disk failure')):
        result = run(rel)
    assert result.rows['claude']['state'] == 'failed' and result.rows['claude']['reason'] == 'instruction_write_failed'
    assert 'synthetic disk failure' in result.rows['claude']['detail'] and 'ahl update' in result.rows['claude']['fix']
    assert homes.claude.read_bytes() == b'# my private rules\nkeep me\n'
    assert json.loads((rel.install_root / 'installation.json').read_text())['version'] == '0.1.0'
    assert (rel.bin / 'ahl').resolve() == result.destination / 'ahl'


def test_a_file_changed_while_updating_is_never_overwritten(rel, homes):
    real = installer.inspect_target
    def racing(path, *args):
        found = real(path, *args)
        if Path(path) == homes.claude:
            homes.claude.write_bytes(b'someone else wrote this meanwhile, longer\n')
        return found
    with patch.object(installer, 'inspect_target', racing):
        row = run(rel).rows['claude']
    assert row['state'] == 'failed' and homes.claude.read_bytes() == b'someone else wrote this meanwhile, longer\n'


def run_main(rel, *flags, version=None, dirs=None):
    archive, checksums, _ = built(rel, version or '0.1.0')
    return installer.main(['--archive', str(archive), '--checksums', str(checksums), '--root', str(rel.install_root),
                           '--bin-dir', str(rel.bin), *flags], skill_dirs=dirs or rel.dirs)


def test_the_opt_out_is_recorded_and_respected_by_a_later_update_until_re_enabled(rel, homes):
    receipt = rel.install_root / 'installation.json'
    run_main(rel, '--no-instructions')
    assert json.loads(receipt.read_text())['instructions'] == dict(enabled=False, agent='both')
    run_main(rel, version='0.2.0')  # a later update names no flag
    assert homes.claude.read_bytes() == b'# my private rules\nkeep me\n' and not homes.codex.exists()
    assert json.loads(receipt.read_text())['instructions']['enabled'] is False
    run_main(rel, '--instructions', version='0.3.0')
    assert b'version=0.3.0' in homes.claude.read_bytes() and homes.codex.exists()
    assert json.loads(receipt.read_text())['instructions']['enabled'] is True
    run_main(rel, version='0.3.0')
    assert json.loads(receipt.read_text())['instructions']['enabled'] is True  # and the choice sticks


def test_an_update_keeps_the_recorded_agent_selection(rel, homes):
    run_main(rel, '--agent', 'claude', dirs=rel.dirs[1:])
    assert json.loads((rel.install_root / 'installation.json').read_text())['instructions']['agent'] == 'claude'
    run_main(rel, version='0.2.0', dirs=rel.dirs[1:])
    assert b'version=0.2.0' in homes.claude.read_bytes() and not homes.codex.exists()


def test_an_instruction_problem_exits_3_after_a_complete_install_and_names_the_exact_fix(rel, homes, capsys):
    homes.codex.write_bytes(b'<!-- agent-harness-lab:end -->\n')
    with pytest.raises(SystemExit) as stopped:
        run_main(rel)
    assert stopped.value.code == 3
    out = capsys.readouterr()
    assert 'Installed 0.1.0' in out.out and 'Instructions updated' in out.out
    assert 'instruction_markers_malformed' in out.err and str(homes.codex) in out.err and 'Fix: Edit' in out.err
    assert (rel.bin / 'ahl').is_symlink() and all((folder / name).is_symlink() for folder in rel.dirs for name in installer.SKILLS)
    assert b'agent-harness-lab:start' in homes.claude.read_bytes()


def test_the_network_path_writes_the_block_too(rel, homes, tmp_path):
    archive, checksums, _ = built(rel, '0.1.0')
    def answer(request, timeout):
        return io.BytesIO(b'{"tag_name":"v0.1.0"}')
    def download(url, target):
        Path(target).write_bytes((archive if url.endswith('.tar.gz') else checksums).read_bytes())
    with patch.object(installer, 'urlopen', side_effect=answer), patch.object(installer, 'fetch', side_effect=download):
        installer.main(['--root', str(rel.install_root), '--bin-dir', str(rel.bin)], skill_dirs=rel.dirs)
    assert b'version=0.1.0' in homes.claude.read_bytes() and homes.codex.exists()


def test_ahl_update_refreshes_the_region_and_remembers_the_opt_out(rel, homes):
    current = run(rel).destination
    archive, checksums, _ = built(rel, '0.2.0')
    def response(request, timeout):
        url = request.full_url
        if url.endswith('/releases/latest'): return io.BytesIO(b'{"tag_name":"v0.2.0"}')
        return io.BytesIO((archive if url.endswith('.tar.gz') else checksums).read_bytes())
    with patch('agent_harness_lab.cli.assets', return_value=current), patch('urllib.request.urlopen', side_effect=response):
        assert updater.main([]) == 0
    assert b'version=0.2.0' in homes.claude.read_bytes() and b'version=0.1.0' not in homes.claude.read_bytes()
    state = json.loads((rel.install_root / 'installation.json').read_text())
    assert state['version'] == '0.2.0' and state['instructions'] == dict(enabled=True, agent='both')


def test_ahl_update_exits_3_after_a_complete_update_when_an_instruction_file_is_refused(rel, homes, capsys):
    current = run(rel).destination
    homes.codex.write_bytes(b'<!-- agent-harness-lab:start -->\n')
    archive, checksums, _ = built(rel, '0.2.0')
    def response(request, timeout):
        url = request.full_url
        if url.endswith('/releases/latest'): return io.BytesIO(b'{"tag_name":"v0.2.0"}')
        return io.BytesIO((archive if url.endswith('.tar.gz') else checksums).read_bytes())
    with patch('agent_harness_lab.cli.assets', return_value=current), patch('urllib.request.urlopen', side_effect=response):
        with pytest.raises(SystemExit) as stopped:
            updater.main([])
    assert stopped.value.code == 3
    assert 'instruction_markers_malformed' in capsys.readouterr().err
    assert (rel.bin / 'ahl').resolve() == rel.install_root / 'releases/0.2.0/ahl' and b'version=0.2.0' in homes.claude.read_bytes()
    assert json.loads((rel.install_root / 'installation.json').read_text())['version'] == '0.2.0'


def test_ahl_update_no_instructions_is_remembered_for_the_next_plain_update(rel, homes):
    current = run(rel, settings=(True, 'both')).destination
    archive, checksums, _ = built(rel, '0.2.0')
    def response(request, timeout):
        url = request.full_url
        if url.endswith('/releases/latest'): return io.BytesIO(b'{"tag_name":"v0.2.0"}')
        return io.BytesIO((archive if url.endswith('.tar.gz') else checksums).read_bytes())
    old = homes.claude.read_bytes()
    with patch('agent_harness_lab.cli.assets', return_value=current), patch('urllib.request.urlopen', side_effect=response):
        assert updater.main(['--no-instructions']) == 0
    assert homes.claude.read_bytes() == old
    assert json.loads((rel.install_root / 'installation.json').read_text())['instructions']['enabled'] is False


def test_rollback_restores_the_file_bytes_launcher_links_and_receipt_exactly(rel, homes):
    first = run(rel, '0.1.0')
    snapshot = {p: p.read_bytes() for p in (homes.claude, homes.codex, rel.install_root / 'installation.json')}
    second = run(rel, '0.2.0')
    assert homes.claude.read_bytes() != snapshot[homes.claude]
    manifest = installer.rollback(rel.install_root, second.backup.name)
    assert {p: p.read_bytes() for p in snapshot} == snapshot
    assert (rel.bin / 'ahl').resolve() == first.destination / 'ahl'
    assert all((folder / name).resolve() == first.destination / 'skills' / name for folder in rel.dirs for name in installer.SKILLS)
    assert len(manifest['files']) == 2 and (second.backup / 'manifest.restored.json').is_file()
    with pytest.raises(ValueError, match='no rollback manifest'):
        installer.rollback(rel.install_root, second.backup.name)


def test_rollback_refuses_to_overwrite_text_changed_since_the_update(rel, homes):
    run(rel, '0.1.0')
    second = run(rel, '0.2.0')
    homes.claude.write_bytes(homes.claude.read_bytes() + b'\nwritten after the update\n')
    changed = homes.claude.read_bytes()
    with pytest.raises(ValueError, match='changed since the installation, so nothing was restored'):
        installer.rollback(rel.install_root, second.backup.name)
    assert homes.claude.read_bytes() == changed and (rel.bin / 'ahl').resolve().parent.name == '0.2.0'


def test_rollback_name_cannot_escape_the_backups_directory(rel, homes):
    run(rel, '0.1.0'); run(rel, '0.2.0')
    for name in ('../backups/install-x', 'install-../..', ''):
        with pytest.raises(ValueError):
            installer.rollback(rel.install_root, name)


def test_the_cli_routes_rollback_of_an_installation_backup(rel, homes, capsys):
    from agent_harness_lab import cli
    first = run(rel, '0.1.0')
    second = run(rel, '0.2.0')
    snapshot = homes.claude.read_bytes()
    with patch('agent_harness_lab.cli.assets', return_value=second.destination):
        assert cli.main(['rollback', second.backup.name]) == 0
    assert 'Restored' in capsys.readouterr().out and homes.claude.read_bytes() != snapshot
    assert (rel.bin / 'ahl').resolve() == first.destination / 'ahl'
    with patch('agent_harness_lab.cli.assets', return_value=second.destination):
        assert cli.main(['rollback', second.backup.name]) == 2


def report(rel, destination, version):
    return installation.inspect(version, destination, Path(os.environ['HOME']))



def states(info):
    return {row['agent']: row['state'] for row in info['instructions']}


def test_doctor_reports_each_target_offline_with_the_exact_fix(rel, homes, tmp_path, monkeypatch):
    result = run(rel)
    monkeypatch.setenv('PATH', str(tmp_path / 'empty-path'))
    with patch('urllib.request.urlopen', side_effect=AssertionError('doctor must not use the network')):
        info = report(rel, result.destination, '0.1.0')
        assert states(info) == {'claude': 'current', 'codex': 'current'} and info['warnings'] == []
        homes.claude.write_bytes(homes.claude.read_bytes().replace(b'version=0.1.0', b'version=0.0.9'))
        homes.codex.write_bytes(b'')
        homes.shared.parent.mkdir(parents=True, exist_ok=True)
        homes.shared.write_bytes(b'<!-- agent-harness-lab:start -->\n')
        info = report(rel, result.destination, '0.1.0')
    assert states(info) == {'claude': 'stale', 'codex': 'absent', 'shared': 'refused'}
    fixes = {w['code']: w['fix'] for w in info['warnings']}
    assert fixes['instruction_region_stale'] == fixes['instruction_region_absent'] == 'ahl update'
    assert 'AGENTS.md' in fixes['instruction_markers_malformed'] and 'ahl update' in fixes['instruction_markers_malformed']
    assert all(w['fix'] and w['message'] for w in info['warnings']) and info['network_used'] is False


def test_doctor_shows_skipped_for_an_opt_out_or_a_missing_directory(rel, homes, tmp_path, monkeypatch):
    result = run(rel, settings=(False, 'both'))
    monkeypatch.setenv('PATH', str(tmp_path / 'empty-path'))
    assert {row['reason'] for row in report(rel, result.destination, '0.1.0')['instructions']} == {'instructions_opted_out'}
    homes.codex.parent.rmdir()
    run(rel, settings=(True, 'both'))
    rows = {row['agent']: row for row in report(rel, result.destination, '0.1.0')['instructions']}
    assert (rows['claude']['state'], rows['codex']['state'], rows['codex']['reason']) == ('current', 'skipped', 'config_directory_missing')


def test_the_doctor_report_and_text_carry_the_instruction_rows(rel, homes, tmp_path, capsys, monkeypatch):
    from agent_harness_lab.onboarding import main
    result = run(rel)
    project = tmp_path / 'project'; project.mkdir()
    monkeypatch.setenv('PATH', str(tmp_path / 'empty-path'))
    with patch('agent_harness_lab.cli.assets', return_value=result.destination), patch('agent_harness_lab.cli.version', return_value='0.1.0'):
        assert doctor(project)['installation']['instructions'][0]['state'] in ('current', 'skipped')
        main(['doctor', str(project)])
    assert f'instructions {homes.claude}: current' in capsys.readouterr().out
