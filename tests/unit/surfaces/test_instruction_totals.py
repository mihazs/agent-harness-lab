"""Synthetic launch closure counts, never runtime-loading evidence."""
import time

import pytest

from agent_harness_lab.audit import instruction_totals
from agent_harness_lab.sources.git_view import Folder


def count(tmp_path, files):
    root = tmp_path / 'project'
    root.mkdir()
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    profile = dict(schema_version=1, runtime='claude-code', version='2.1.317', task_cwd='.')
    with Folder(tmp_path, time.monotonic() + 30) as folder:
        return instruction_totals.claude(profile, folder, 'project')


def test_counts_imported_non_instruction_filename_once(tmp_path):
    report = count(tmp_path, {'CLAUDE.md': '@detail.md\n@detail.md\n', 'detail.md': 'Must verify.\n'})
    assert (report['status'], report['files'], report['bytes'], report['lines']) == ('declared', 2, 35, 3)
    assert report['observed_loading'] == 'unknown' and report['publication_allowed'] is False


@pytest.mark.parametrize('files, reason', [
    ({'CLAUDE.md': '@missing.md\n'}, 'import_unresolved'),
    ({'CLAUDE.md': '@detail.md\n', 'detail.md': '@CLAUDE.md\n'}, 'import_cycle'),
    ({'CLAUDE.md': '<!-- @detail.md -->\n', 'detail.md': 'hidden\n'}, 'import_context_uncertain'),
])
def test_unresolved_imports_do_not_get_totals(tmp_path, files, reason):
    report = count(tmp_path, files)
    assert report['status'] == 'unknown' and reason in report['reasons']
    assert report['bytes'] is None and report['lines'] is None and report['files'] is None


def test_import_link_is_never_followed(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'CLAUDE.md').write_text('@detail.md\n')
    (tmp_path / 'outside.md').write_text('PRIVATE_SYNTHETIC\n')
    (root / 'detail.md').symlink_to(tmp_path / 'outside.md')
    profile = dict(schema_version=1, runtime='claude-code', version='2.1.317', task_cwd='.')
    with Folder(tmp_path, time.monotonic() + 30) as folder:
        with pytest.raises(ValueError):
            instruction_totals.claude(profile, folder, 'project')


def test_mismatched_inventory_hash_blocks_total(tmp_path):
    from agent_harness_lab.sources.schema import digest

    root = tmp_path / 'project'
    root.mkdir()
    (root / 'CLAUDE.md').write_text('synthetic\n')
    profile = dict(schema_version=1, runtime='claude-code', version='2.1.317', task_cwd='.')
    with Folder(tmp_path, time.monotonic() + 30) as folder:
        report = instruction_totals.claude(profile, folder, 'project', expected={digest('CLAUDE.md'): '0' * 64})
    assert report['status'] == 'unknown' and report['reasons'] == ['inventory_content_changed']
    assert report['bytes'] is None


def test_private_launch_file_keeps_total_unknown(tmp_path):
    report = count(tmp_path, {'CLAUDE.md': 'public\n', 'CLAUDE.local.md': 'private\n'})
    assert report['status'] == 'unknown'
    assert 'private_launch_file_not_captured' in report['reasons']
    assert report['bytes'] is None


def test_shape_hints_include_imported_instructions_and_no_content(tmp_path):
    report = count(tmp_path, {'CLAUDE.md': '@detail.md\n', 'detail.md': 'Must verify with pytest.\n'})
    assert report['shape'] == dict(constraints=True, verification_commands=True, procedure_sections=False)
    assert 'Must verify' not in repr(report)


def test_numbered_procedure_is_reported_but_not_quoted(tmp_path):
    report = count(tmp_path, {'CLAUDE.md': '# Procedure\n' + ''.join(f'{i}. step\n' for i in range(1, 6))})
    assert report['shape']['procedure_sections'] is True
    assert '# Procedure' not in repr(report)


def test_unknown_closure_does_not_assert_shape(tmp_path):
    report = count(tmp_path, {'CLAUDE.md': '@missing.md\n'})
    assert report['shape'] is None


def test_oversized_launch_file_does_not_become_zero_total(tmp_path):
    from agent_harness_lab.sources.claude_instructions import SKIPPED_BYTES

    report = count(tmp_path, {'CLAUDE.md': 'x\n' * (SKIPPED_BYTES // 2 + 1)})
    assert report['status'] == 'unknown'
    assert 'launch_file_unsettled' in report['reasons']
    assert report['bytes'] is None and report['lines'] is None and report['files'] is None


@pytest.mark.parametrize('command', ['npm run build', 'cargo check', 'make verify', 'go build'])
def test_common_build_and_verification_commands_are_detected(tmp_path, command):
    report = count(tmp_path, {'CLAUDE.md': f'Must run {command}.\n'})
    assert report['shape']['verification_commands'] is True
